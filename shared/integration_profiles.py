"""Safe, opt-in public identities for verified publisher connections."""
import re

VISIBILITY_FIELDS = tuple(
    f"{provider}_show_on_{placement}"
    for provider in ("telegram", "threads", "pinterest")
    for placement in ("user_page", "articles_pages")
)


def public_profile(provider, record):
    """Allowlist public names only; never retain provider IDs or credentials."""
    if not isinstance(record, dict):
        return None
    username = record.get("username")
    pattern = r"[A-Za-z0-9_]+" if provider == "telegram" else r"[A-Za-z0-9_.]+"
    valid_username = (isinstance(username, str) and len(username) <= 64
                      and re.fullmatch(pattern, username) and username not in (".", ".."))
    result = {"username": username} if valid_username else {}
    title = record.get("title")
    if provider == "telegram" and isinstance(title, str) and title.strip():
        result["title"] = title.strip()[:255]
    return result or None


def visible_profile(provider, record):
    if not record.get(f"has_{provider}_integration"):
        return None
    if not any(record.get(f"{provider}_show_on_{placement}") is True
               for placement in ("user_page", "articles_pages")):
        return None
    return public_profile(provider, record.get(f"{provider}_public_profile"))
