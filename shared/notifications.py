"""Telegram logging destination used by both application Lambdas."""

import copy
import json
import logging
import os
import sys
from urllib.request import Request, urlopen


def get_access_log_message(request, status) -> str:
    client_ip = request.client.host if request.client else None
    protocol = f"HTTP/{request.scope.get('http_version', '1.1')}"
    return (
        f"{client_ip or '-'} - "
        f"{json.dumps(f'{request.method} {request.url} {protocol}', ensure_ascii=False)} {status} - "
        f"{json.dumps(request.headers.get('referer') or '-', ensure_ascii=False)} "
        f"{json.dumps(request.headers.get('user-agent') or '-', ensure_ascii=False)}"
    )


class TelegramFormatter(logging.Formatter):
    """Include only the log record's context as JSON alongside the message."""

    def format(self, record):
        # shared_utils imports this module to configure its logger.
        from shared_utils import config

        record = copy.copy(record)
        record.exc_text = None  # Another handler may already have formatted it.
        record.stack_info = None
        text = super().format(record)
        context = json.dumps(getattr(record, "context", {}), ensure_ascii=False, default=str)
        return f"[{config.get('app_stage')}] {text} {context}"


class TelegramHandler(logging.Handler):
    """Synchronous, best-effort delivery; no background work survives Lambda freeze."""

    def __init__(self, token, chat_id, level=logging.INFO):
        super().__init__(level)
        self._token = token
        self._chat_id = chat_id
        self.setFormatter(TelegramFormatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        try:
            text = self.format(record).replace(self._token, "[REDACTED]")
            # Bound both characters and UTF-16 code units, including emoji.
            text = text.encode("utf-16-le")[:8000].decode("utf-16-le", errors="ignore")
            payload = json.dumps({"chat_id": self._chat_id, "text": text,
                                  "link_preview_options": {"is_disabled": True}}).encode()
            request = Request(f"https://api.telegram.org/bot{self._token}/sendMessage",
                              data=payload, headers={"Content-Type": "application/json"},
                              method="POST")
            with urlopen(request, timeout=2) as response:
                if not json.load(response).get("ok"):
                    self.handleError(record)
        except Exception:
            self.handleError(record)

    def handleError(self, record):
        # Never log from emit/handleError: that can recurse or deadlock. The
        # default implementation can expose the token in a failed request URL.
        try:
            sys.stderr.write("Telegram log delivery failed\n")
        except Exception:
            pass


def configure_telegram_logging(logger):
    """Attach once at startup to the app logger, leaving third-party logs alone."""
    if any(isinstance(handler, TelegramHandler) for handler in logger.handlers):
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    level_name = os.environ.get("TELEGRAM_LOG_LEVEL", "INFO").upper()
    if not token or not chat_id or level_name == "OFF":
        return
    levels = {name: getattr(logging, name) for name in
              ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")}
    if level_name not in levels:
        sys.stderr.write("Invalid TELEGRAM_LOG_LEVEL; Telegram logging disabled\n")
        return
    handler = TelegramHandler(token, chat_id, levels[level_name])
    logger.addHandler(handler)
    # The console handler retains its threshold while Telegram can use DEBUG.
    logger.setLevel(min(logger.getEffectiveLevel(), handler.level))
