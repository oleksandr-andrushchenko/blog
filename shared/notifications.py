"""Telegram logging destination used by both application Lambdas."""

import copy
import json
import logging
import sys
from urllib.request import Request, urlopen

from api_route_metadata import API_URL_ROUTES
from app_config import get_telegram_bot_token, get_telegram_chat_id, get_telegram_log_level
from web_route_metadata import WEB_AUTH_URL_ROUTES


def _access_log_url(value):
    from urllib.parse import urlsplit, urlunsplit
    try:
        parts = urlsplit(str(value))
    except ValueError:
        # Referrers are untrusted and must never break access logging.
        return "[invalid URL]"
    if parts.path.rstrip("/") in {
        API_URL_ROUTES["threads-callback"], API_URL_ROUTES["login-link-callback"],
        WEB_AUTH_URL_ROUTES["login-callback"], WEB_AUTH_URL_ROUTES["logout-callback"],
    }:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
    return str(value)


def get_access_log(request, status: int) -> tuple[int, str]:
    if status >= 500:
        level = logging.ERROR
    elif status >= 400 and status not in [404, 405]:
        level = logging.WARNING
    else:
        level = logging.DEBUG

    client_ip = request.client.host if request.client else None
    protocol = f"HTTP/{request.scope.get('http_version', '1.1')}"
    message = (
        f"{client_ip or '-'} - "
        f"{json.dumps(f'{request.method} {_access_log_url(request.url)} {protocol}', ensure_ascii=False)} {status} - "
        f"{json.dumps(_access_log_url(request.headers.get('referer') or '-'), ensure_ascii=False)} "
        f"{json.dumps(request.headers.get('user-agent') or '-', ensure_ascii=False)}"
    )
    return level, message


class ContextFormatter(logging.Formatter):
    """Expose structured action context in console/CloudWatch as well as Telegram."""

    def format(self, record):
        text = super().format(record)
        if hasattr(record, 'context'):
            text += ' ' + json.dumps(record.context, ensure_ascii=False, default=str)
        return text


class TelegramFormatter(logging.Formatter):
    """Include only the log record's context as JSON alongside the message."""

    def format(self, record):
        from app_config import get_config

        record = copy.copy(record)
        record.exc_text = None  # Another handler may already have formatted it.
        record.stack_info = None
        text = super().format(record)
        context = json.dumps(getattr(record, "context", {}), ensure_ascii=False, default=str)
        if record.levelno >= logging.ERROR:
            bullet = "🔴"
        elif record.levelno >= logging.WARNING:
            bullet = "🟠"
        elif record.levelno >= logging.INFO:
            bullet = "🔵"
        else:
            bullet = "⚪"
        return f"{bullet} [{get_config().get('app_stage')}] {text} {context}"


def _telegram_message_parts(text):
    """Split without dropping characters or cutting an emoji's UTF-16 pair."""
    chunk = []
    units = 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > 4000:
            yield ''.join(chunk)
            chunk = []
            units = 0
        chunk.append(char)
        units += width
    if chunk:
        yield ''.join(chunk)


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
            for part in _telegram_message_parts(text):
                payload = json.dumps({"chat_id": self._chat_id, "text": part,
                                      "link_preview_options": {"is_disabled": True}}).encode()
                request = Request(f"https://api.telegram.org/bot{self._token}/sendMessage",
                                  data=payload, headers={"Content-Type": "application/json"},
                                  method="POST")
                with urlopen(request, timeout=2) as response:
                    if not json.load(response).get("ok"):
                        self.handleError(record)
                        return
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
    token = get_telegram_bot_token()
    chat_id = get_telegram_chat_id()
    level_name = get_telegram_log_level()
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
