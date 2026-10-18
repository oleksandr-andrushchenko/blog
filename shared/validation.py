import re
import unicodedata
from urllib.parse import urlparse


def normalize_slug(value: str) -> str:
    value = unicodedata.normalize("NFC", value.lower().strip())
    value = re.sub(r"[^\w\s-]", "", value)
    return re.sub(r"[\s_-]+", "-", value).strip("-")


def validate_catalog_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    if any(char == "\ufffd" or unicodedata.category(char) == "Cs"
           or (unicodedata.category(char) == "Cc" and char not in "\t\n\r") for char in value):
        raise ValueError("text contains invalid encoding or control characters")
    return value


def validate_category_slug(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError(f"unsupported article category: {value}")
    return value


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
