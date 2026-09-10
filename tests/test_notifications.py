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


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bot-secret",
                                         "TELEGRAM_CHAT_ID": "-123",
                                         "TELEGRAM_LOG_LEVEL": "INFO"})
        self.env.start()
        self.addCleanup(self.env.stop)
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
                with patch.dict(os.environ, {"TELEGRAM_LOG_LEVEL": threshold}):
                    notifications.configure_telegram_logging(self.logger)
                    notifications.configure_telegram_logging(self.logger)
                self.assertEqual(len(self.logger.handlers), 1)
                for level in [10, 20, 30, 40, 50]:
                    self.logger.log(level, "Event")
                self.assertEqual(send.call_count, (60 - getattr(logging, threshold)) // 10)

    @patch("notifications.urlopen")
    def test_disabled_and_invalid(self, send):
        for config in [{"TELEGRAM_BOT_TOKEN": ""}, {"TELEGRAM_CHAT_ID": ""},
                       {"TELEGRAM_LOG_LEVEL": "OFF"}, {"TELEGRAM_LOG_LEVEL": "INVALID"}]:
            with patch.dict(os.environ, config), patch("sys.stderr", new=io.StringIO()):
                notifications.configure_telegram_logging(self.logger)
                self.assertFalse(self.logger.handlers)
        send.assert_not_called()

    @patch("notifications.urlopen")
    def test_payload_and_length(self, send):
        send.return_value = io.BytesIO(b'{"ok": true}')
        notifications.configure_telegram_logging(self.logger)
        self.logger.info("New article %s", "😀" * 5000)
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["chat_id"], "-123")
        self.assertLessEqual(len(payload["text"].encode("utf-16-le")), 8000)
        self.assertNotIn("parse_mode", payload)
        self.assertEqual(send.call_args.kwargs["timeout"], 2)

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
        self.assertIn("article_id: 123", text)
        self.assertIn("Exception: ValueError", text)
        self.assertIn("password: private", text)
        self.assertIn("private exception text", text)
        self.assertNotIn("bot-secret", text)
        self.assertIn("private exception text", console.getvalue())

    @patch("notifications.urlopen")
    def test_child_logger_and_console_threshold(self, send):
        self.logger.setLevel(logging.INFO)
        console = io.StringIO()
        handler = logging.StreamHandler(console)
        handler.setLevel(logging.INFO)
        self.logger.addHandler(handler)
        with patch.dict(os.environ, {"TELEGRAM_LOG_LEVEL": "DEBUG"}):
            notifications.configure_telegram_logging(self.logger)
        send.return_value = io.BytesIO(b'{"ok":true}')
        logging.getLogger("telegram_test.child").debug("Child debug")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(console.getvalue(), "")

    @patch("notifications.urlopen")
    def test_request_details_are_sent(self, send):
        send.return_value = io.BytesIO(b'{"ok":true}')
        notifications.configure_telegram_logging(self.logger)
        self.logger.info("HTTP exception", extra={
            "method": "GET", "path": "/missing", "status": 404,
            "hostname": "example.execute-api.amazonaws.com",
            "client_ip": "192.0.2.10", "user_agent": "ExampleBrowser/1.0\nforged line",
        })
        text = json.loads(send.call_args.args[0].data)["text"]
        for field in ("method: GET", "path: /missing", "status: 404", "client_ip: 192.0.2.10",
                      "hostname: example.execute-api.amazonaws.com",
                      "user_agent: ExampleBrowser/1.0 forged line"):
            self.assertIn(field, text)
