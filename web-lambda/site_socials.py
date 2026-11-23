"""Compose public website social links without consulting user connections."""
from urllib.parse import urlsplit

from app_config import config
from validation import validate_http_url


PLATFORMS = (
    ("site_pinterest_profile_url", "pinterest", "Pinterest", {"pinterest.com", "www.pinterest.com"}),
    ("site_telegram_channel_url", "telegram", "Telegram", {"t.me", "telegram.me"}),
    ("site_threads_profile_url", "threads", "Threads", {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}),
)


def website_social_links():
    links = [dict(link) for link in config.get("contact_socials") or []]
    for key, icon, name, hosts in PLATFORMS:
        try:
            value = validate_http_url(config.get(key))
            if not value:
                continue
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username is not None
                    or parsed.password is not None or parsed.port is not None or not parsed.path.strip("/")
                    or any(char.isspace() for char in value)):
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        # Explicit website settings replace legacy entries for the same platform.
        links = [link for link in links if link.get("icon") != icon and link.get("url", "").rstrip("/") != value]
        links.append({"icon": icon, "name": name, "url": value})
    return links
