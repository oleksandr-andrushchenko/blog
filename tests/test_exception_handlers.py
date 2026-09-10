"""Exercise both Lambdas' registered exception handlers through ASGI routing."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for directory in ("shared", "api-lambda", "web-lambda"):
    sys.path.insert(0, str(project_root / directory))

from starlette.exceptions import HTTPException
from web import Application, Request
import shared_utils


class ExceptionHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = []
        with patch.dict(os.environ, {"TELEGRAM_LOG_LEVEL": "OFF"}):
            for service in ("api", "web"):
                spec = importlib.util.spec_from_file_location(
                    f"{service}_app_exception_tests", project_root / f"{service}-lambda/app.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                cls.modules.append(module)

    def invoke(self, module, exception, path="/failure"):
        app = Application()
        app.exception_handlers.update(module.app.exception_handlers)
        app.url_routes = [*module.app.routes, *module.app.url_routes]

        @app.get("/failure", name="failure")
        async def failure():
            raise exception

        async def run():
            messages = []

            async def receive():
                return {"type": "http.request", "body": b""}

            async def send(message):
                messages.append(message)

            scope = {"type": "http", "method": "GET", "path": path,
                     "root_path": "", "query_string": b"token=private-query", "scheme": "http",
                     "server": ("testserver", 80),
                     "client": ("192.0.2.10", 12345),
                     "headers": [(b"content-type", b"application/json"),
                                 (b"host", b"example.execute-api.amazonaws.com"),
                                 (b"user-agent", b"ExampleBrowser/1.0"),
                                 (b"authorization", b"Bearer private-token"),
                                 (b"x-forwarded-for", b"spoofed-ip")],
                     "aws_request_id": "request-123"}
            # The harness bypasses middleware, so supply the real renderer with
            # the template paths and request context normally set by the web app.
            with patch.dict(os.environ, {
                "FUNCTION_TEMPLATES_DIR": str(project_root / "web-lambda/templates"),
            }):
                templates = shared_utils.get_jinja2_env()
            templates.globals["request"] = Request(scope)
            with patch.object(shared_utils, "jinja2_env", return_value=templates):
                try:
                    await app(scope, receive, send)
                except Exception as raised:
                    # Starlette re-raises unhandled errors after sending the response.
                    self.assertIs(raised, exception)
            return messages

        return asyncio.run(run())

    def assert_response_format(self, module, messages, status):
        headers = dict(messages[0]["headers"])
        body = b"".join(message.get("body", b"") for message in messages)
        if module.__name__.startswith("api_"):
            self.assertEqual(headers[b"content-type"], b"application/json")
            self.assertEqual(json.loads(body)["code"], status)
        else:
            self.assertTrue(headers[b"content-type"].startswith(b"text/html"))
            self.assertIn(f"<h1>{status} - ".encode(), body)
            self.assertIn(b"Back home", body)

    def test_http_exceptions_log_once_at_correct_level(self):
        for module in self.modules:
            for status in (400, 404, 500, 503):
                with self.subTest(module=module.__name__, status=status), patch.object(module, "logger") as logger:
                    messages = self.invoke(module, HTTPException(status, "detail"))
                    self.assertEqual(messages[0]["status"], status)
                    self.assert_response_format(module, messages, status)
                    self.assertEqual(logger.error.call_count, int(status >= 500))
                    self.assertEqual(logger.info.call_count, int(status < 500))
                    self.assertEqual(len(logger.mock_calls), 1)

    def test_unhandled_exception_logs_once_and_hides_details(self):
        for module in self.modules:
            with self.subTest(module=module.__name__), patch.object(module, "logger") as logger:
                exc = ValueError("private failure details")
                messages = self.invoke(module, exc)
                self.assertEqual(messages[0]["status"], 500)
                self.assert_response_format(module, messages, 500)
                self.assertIn((b"cache-control", b"no-store"), messages[0]["headers"])
                body = b"".join(message.get("body", b"") for message in messages)
                self.assertNotIn(b"private failure details", body)
                logger.error.assert_called_once()
                self.assertEqual(len(logger.mock_calls), 1)
                self.assertIs(logger.error.call_args.kwargs["exc_info"], exc)
                self.assertEqual(logger.error.call_args.kwargs["extra"]["request_id"], "request-123")

    def test_missing_endpoint_includes_request_context(self):
        for module in self.modules:
            with self.subTest(module=module.__name__), patch.object(module, "logger") as logger:
                messages = self.invoke(module, None, path="/missing-endpoint")
                self.assertEqual(messages[0]["status"], 404)
                logger.info.assert_called_once()
                context = logger.info.call_args.kwargs["extra"]
                self.assertEqual(context["route"], "unresolved")
                self.assertEqual(context["service"], module.__name__.split("_")[0])
                self.assertEqual(context["request_id"], "request-123")
                self.assertRegex(context["access_log"],
                                 r'^192\.0\.2\.10 - - \[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} \+0000\] ')
                self.assertIn(
                    '"GET http://example.execute-api.amazonaws.com/missing-endpoint?token=private-query HTTP/1.1" '
                    '404 - "-" "ExampleBrowser/1.0"', context["access_log"])
                self.assertNotIn("private-token", str(context))
                self.assertNotIn("spoofed", str(context))
