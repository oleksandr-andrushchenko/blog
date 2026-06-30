import re
from urllib.parse import urlparse


def validate_email_address(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("Invalid email address")
    return value


def validate_http_url(value: str | None) -> str | None:
    if value is None:
        return value
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Website must be a valid http or https URL")
    return value.rstrip("/")
