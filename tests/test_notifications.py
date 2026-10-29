"""Offline tests for logging routing, safe delivery, and request failures."""
import io
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(project_root / "shared"))
import notifications
from app_config import config as app_config
from web import Request


class AccessLogTests(unittest.TestCase):
    def test_web_callback_codes_are_redacted_from_requests_and_referrers(self):
        for path in ('/login-callback', '/logout-callback', '/login-callback/',
                     '/auth/login-link/callback', '/integrations/threads/callback'):
            with self.subTest(path=path):
                request = self.request(path)
                request.scope['query_string'] = b'code=private-code&state=private-state'
                request.scope['headers'] = [(b'referer',
                    b'https://catalog.example/login-callback?code=referrer-code&state=referrer-state')]
                level, message = notifications.get_access_log(request, 400)
                self.assertEqual(level, logging.WARNING)
                self.assertIn(f'GET https://catalog.example{path} HTTP/1.1', message)
                self.assertNotIn('private-', message)
                self.assertNotIn('referrer-', message)
                self.assertNotIn('?', message)

    def request(self, path, method='GET'):
        return Request({'type': 'http', 'method': method, 'scheme': 'https',
                        'server': ('catalog.example', 443), 'path': path,
                        'query_string': b'source=discovery', 'headers': []})

    def test_all_404s_and_405s_log_at_debug_without_changing_message(self):
        for status in (404, 405):
            for path in ('/articles/missing', '/users/missing', '/.well-known/future-discovery.json'):
                for method in ('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'OPTIONS'):
                    with self.subTest(status=status, path=path, method=method):
                        level, message = notifications.get_access_log(self.request(path, method), status)
                        self.assertEqual(level, logging.DEBUG)
                        self.assertIn(f'{method} https://catalog.example{path}?source=discovery', message)
                        self.assertIn(f' {status} - ', message)

    def test_other_error_statuses_keep_their_severity(self):
        for status in (400, 401, 403, 429, 500, 502, 503):
            with self.subTest(status=status):
                self.assertEqual(notifications.get_access_log(self.request('/articles/missing'), status)[0],
                                 logging.ERROR if status >= 500 else logging.WARNING)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(app_config, {"telegram_bot_token": "bot-secret",
                                             "telegram_chat_id": "-123",
                                             "telegram_log_level": "INFO"})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.logger = logging.getLogger("telegram_test")
        self.logger.handlers = []
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        self.addCleanup(self.logger.handlers.clear)

    @patch("notifications.urlopen")
    def test_thresholds_and_idempotence(self, send):
        for threshold in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            with self.subTest(threshold=threshold):
                self.logger.handlers.clear()
                send.reset_mock()
                send.side_effect = lambda *a, **k: io.BytesIO(b'{"ok":true}')
                with patch.dict(app_config, {"telegram_log_level": threshold}):
                    notifications.configure_telegram_logging(self.logger)
                    notifications.configure_telegram_logging(self.logger)
                self.assertEqual(len(self.logger.handlers), 1)
                for level in [10, 20, 30, 40, 50]:
                    self.logger.log(level, "Event")
                self.assertEqual(send.call_count, (60 - getattr(logging, threshold)) // 10)

    @patch("notifications.urlopen")
    def test_disabled_and_invalid(self, send):
        for config in [{"telegram_bot_token": ""}, {"telegram_chat_id": ""},
                       {"telegram_log_level": "OFF"}, {"telegram_log_level": "INVALID"}]:
            with patch.dict(app_config, config), patch("sys.stderr", new=io.StringIO()):
                notifications.configure_telegram_logging(self.logger)
                self.assertFalse(self.logger.handlers)
        send.assert_not_called()

    @patch("notifications.urlopen")
    def test_payload_and_length(self, send):
        send.side_effect = lambda *a, **k: io.BytesIO(b'{"ok":true}')
        notifications.configure_telegram_logging(self.logger)
        self.logger.info("New article %s", "😀" * 5000)
        parts = []
        self.assertEqual(send.call_count, 3)
        for call in send.call_args_list:
            payload = json.loads(call.args[0].data)
            self.assertEqual(payload["chat_id"], "-123")
            self.assertLessEqual(len(payload["text"].encode("utf-16-le")), 8000)
            self.assertNotIn("parse_mode", payload)
            self.assertEqual(call.kwargs["timeout"], 2)
            parts.append(payload['text'])
        record = self.logger.makeRecord(self.logger.name, logging.INFO, '', 0, 'New article %s',
                                       ('😀' * 5000,), None)
        self.assertEqual(''.join(parts), self.logger.handlers[0].format(record))

    def test_message_split_preserves_characters_at_boundaries(self):
        for text in ('a' * 4000, 'a' * 3999 + '😀' + 'b' * 4000, '😀' * 4000):
            with self.subTest(length=len(text)):
                parts = list(notifications._telegram_message_parts(text))
                self.assertEqual(''.join(parts), text)
                self.assertTrue(all(0 < len(part.encode('utf-16-le')) <= 8000 for part in parts))

    @patch("notifications.urlopen")
    def test_oversized_exception_keeps_final_cause_and_console_trace(self, send):
        send.side_effect = lambda *a, **k: io.BytesIO(b'{"ok":true}')
        console = io.StringIO()
        self.logger.addHandler(logging.StreamHandler(console))
        notifications.configure_telegram_logging(self.logger)
        try:
            raise ValueError("underlying failure bot-secret")
        except ValueError:
            self.logger.exception("Long preamble " + "😀" * 5000)
        parts = [json.loads(call.args[0].data)['text'] for call in send.call_args_list]
        text = ''.join(parts)
        self.assertIn("Long preamble " + "😀" * 5000, text)
        self.assertTrue(text.endswith("ValueError: underlying failure [REDACTED] {}"))
        self.assertTrue(all(len(part.encode('utf-16-le')) <= 8000 for part in parts))
        self.assertNotIn("bot-secret", text)
        self.assertIn("Long preamble " + "😀" * 5000, console.getvalue())
        self.assertIn("ValueError: underlying failure bot-secret", console.getvalue())

    @patch("notifications.urlopen")
    def test_short_message_is_sent_once(self, send):
        send.return_value = io.BytesIO(b'{"ok":true}')
        notifications.configure_telegram_logging(self.logger)
        self.logger.error("Short failure")
        self.assertEqual(send.call_count, 1)
        text = json.loads(send.call_args.args[0].data)["text"]
        self.assertIn("ERROR telegram_test: Short failure {}", text)
        self.assertNotIn("trimmed", text)

    @patch("notifications.urlopen")
    def test_payload_bullet_matches_log_level(self, send):
        send.side_effect = lambda *a, **k: io.BytesIO(b'{"ok":true}')
        with patch.dict(app_config, {"telegram_log_level": "DEBUG"}):
            notifications.configure_telegram_logging(self.logger)

        for level, bullet in [
            (logging.DEBUG, "⚪"),
            (logging.INFO, "🔵"),
            (logging.WARNING, "🟠"),
            (logging.ERROR, "🔴"),
            (logging.CRITICAL, "🔴"),
        ]:
            with self.subTest(level=level):
                send.reset_mock()
                self.logger.log(level, "Event")
                text = json.loads(send.call_args.args[0].data)["text"]
                self.assertTrue(text.startswith(f"{bullet} "))

    @patch("notifications.urlopen")
    def test_failures_do_not_recurse_or_escape(self, send):
        notifications.configure_telegram_logging(self.logger)
        for result in [OSError("bot-secret"), io.BytesIO(b'{"ok":false}'), io.BytesIO(b'invalid')]:
            send.reset_mock()
            send.side_effect = result if isinstance(result, Exception) else None
            send.return_value = result
            with patch("sys.stderr", new=io.StringIO()) as stderr:
                self.logger.error("Something failed")
            self.assertEqual(send.call_count, 1)
            self.assertEqual(stderr.getvalue(), "Telegram log delivery failed\n")

    @patch("notifications.urlopen")
    def test_formatter_does_not_mutate_console_record(self, send):
        console = io.StringIO()
        handler = logging.StreamHandler(console)
        handler.setLevel(logging.ERROR)
        self.logger.addHandler(handler)
        notifications.configure_telegram_logging(self.logger)
        send.return_value = io.BytesIO(b'{"ok":true}')
        try:
            raise ValueError("private exception text")
        except ValueError:
            self.logger.exception("Failed bot-secret", extra={"article_id": "123", "password": "private"})
        text = json.loads(send.call_args.args[0].data)["text"]
        self.assertNotIn("article_id", text)
        self.assertNotIn("password", text)
        self.assertIn("Traceback (most recent call last):", text)
        self.assertIn("ValueError: private exception text", text)
        self.assertTrue(text.endswith(" {}"))
        self.assertIn("private exception text", text)
        self.assertNotIn("bot-secret", text)
        self.assertIn("private exception text", console.getvalue())
        self.assertIn("Traceback (most recent call last):", console.getvalue())
        self.assertIn("Failed bot-secret", console.getvalue())

    @patch("notifications.urlopen")
    def test_child_logger_and_console_threshold(self, send):
        self.logger.setLevel(logging.INFO)
        console = io.StringIO()
        handler = logging.StreamHandler(console)
        handler.setLevel(logging.INFO)
        self.logger.addHandler(handler)
        with patch.dict(app_config, {"telegram_log_level": "DEBUG"}):
            notifications.configure_telegram_logging(self.logger)
        send.return_value = io.BytesIO(b'{"ok":true}')
        logging.getLogger("telegram_test.child").debug("Child debug")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(console.getvalue(), "")

    @patch("notifications.urlopen")
    def test_only_context_is_sent_as_json(self, send):
        send.return_value = io.BytesIO(b'{"ok":true}')
        notifications.configure_telegram_logging(self.logger)
        context = {
            "method": "GET", "path": "/missing", "status": 404,
            "hostname": "example.execute-api.amazonaws.com",
            "client_ip": "192.0.2.10", "user_agent": "ExampleBrowser/1.0\nforged line",
            "enabled": True, "value": None, "name": "Привіт",
        }
        self.logger.info("HTTP exception", extra={"context": context, "ignored": "excluded-value"})
        text = json.loads(send.call_args.args[0].data)["text"]
        serialized_context = text.split("HTTP exception ", 1)[1]
        self.assertEqual(json.loads(serialized_context), context)
        self.assertIn("Привіт", serialized_context)
        self.assertNotIn("\n", serialized_context)
        self.assertNotIn("excluded-value", text)

    @patch("notifications.urlopen")
    def test_exception_context_is_serialized_as_a_json_string(self, send):
        send.return_value = io.BytesIO(b'{"ok":true}')
        notifications.configure_telegram_logging(self.logger)
        self.logger.error("Failed", extra={"context": ValueError("Invalid value")})
        text = json.loads(send.call_args.args[0].data)["text"]
        self.assertEqual(json.loads(text.split("Failed ", 1)[1]), "Invalid value")
        self.assertNotIn("Traceback (most recent call last):", text)
