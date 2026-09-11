"""Exercise both Lambdas' registered exception handlers through ASGI routing."""
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for directory in ("shared", "api-lambda", "web-lambda"):
    sys.path.insert(0, str(project_root / directory))

from starlette.exceptions import HTTPException
from web import Application, Request, Response
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

    def invoke(self, module, exception, path="/failure", status=200):
        app = Application()
        app.exception_handlers.update(module.app.exception_handlers)
        app.middleware("http")(module.access_log_middleware)
        app.url_routes = [*module.app.routes, *module.app.url_routes]

        @app.get("/failure", name="failure")
        async def failure():
            if exception is not None:
                raise exception
            return Response(status_code=status)

        async def run():
            messages = []

            request_sent = False

            async def receive():
                nonlocal request_sent
                if request_sent:
                    await asyncio.Event().wait()
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}

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
            # The harness includes only access logging middleware; supply the renderer with
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

    def test_http_exceptions_log_access_once_at_status_level(self):
        for module in self.modules:
            for status in (400, 404, 500, 503):
                with self.subTest(module=module.__name__, status=status), patch.object(module, "logger") as logger:
                    messages = self.invoke(module, HTTPException(status, "detail"))
                    self.assertEqual(messages[0]["status"], status)
                    self.assert_response_format(module, messages, status)
                    logger.log.assert_called_once()
                    logger.error.assert_not_called()
                    level, access_log = logger.log.call_args.args
                    self.assertEqual(level, logging.WARNING if status < 500 else logging.ERROR)
                    self.assertIn(f'HTTP/1.1" {status} ', access_log)
                    self.assertEqual(len(logger.mock_calls), 1)

    def test_unhandled_exception_logs_access_and_traceback_and_hides_details(self):
        for module in self.modules:
            with self.subTest(module=module.__name__), patch.object(module, "logger") as logger:
                exc = ValueError("private failure details")
                messages = self.invoke(module, exc)
                self.assertEqual(messages[0]["status"], 500)
                self.assert_response_format(module, messages, 500)
                body = b"".join(message.get("body", b"") for message in messages)
                self.assertNotIn(b"private failure details", body)
                logger.error.assert_called_once()
                logger.log.assert_called_once()
                self.assertEqual(len(logger.mock_calls), 2)
                level, access_log = logger.log.call_args.args
                self.assertEqual(level, logging.ERROR)
                self.assertIn('HTTP/1.1" 500 ', access_log)
                self.assertEqual(logger.error.call_args.args, ("Unhandled request exception",))
                self.assertIs(logger.error.call_args.kwargs["exc_info"], exc)

    def test_missing_endpoint_includes_request_context(self):
        for module in self.modules:
            with self.subTest(module=module.__name__), patch.object(module, "logger") as logger:
                messages = self.invoke(module, None, path="/missing-endpoint")
                self.assertEqual(messages[0]["status"], 404)
                logger.log.assert_called_once()
                logger.error.assert_not_called()
                level, access_log = logger.log.call_args.args
                self.assertEqual(level, logging.WARNING)
                self.assertRegex(access_log, r'^192\.0\.2\.10 - ')
                self.assertIn(
                    '"GET http://example.execute-api.amazonaws.com/missing-endpoint?token=private-query HTTP/1.1" '
                    '404 - "-" "ExampleBrowser/1.0"', access_log)
                self.assertNotIn("private-token", access_log)
                self.assertNotIn("spoofed", access_log)

    def test_success_and_redirect_responses_log_access_at_status_level(self):
        for module in self.modules:
            for status in (200, 204, 301, 308):
                with self.subTest(module=module.__name__, status=status), patch.object(module, "logger") as logger:
                    messages = self.invoke(module, None, status=status)
                    self.assertEqual(messages[0]["status"], status)
                    logger.log.assert_called_once()
                    logger.error.assert_not_called()
                    level, access_log = logger.log.call_args.args
                    self.assertEqual(level, logging.DEBUG if status < 300 else logging.INFO)
                    self.assertIn(f'HTTP/1.1" {status} ', access_log)
                    self.assertEqual(len(logger.mock_calls), 1)
