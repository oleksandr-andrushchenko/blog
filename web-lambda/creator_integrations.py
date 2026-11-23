"""Public creator identities from the already loaded user, without connection reads."""
from integration_profiles import public_profile


def creator_integration_links(user, placement):
    if user is None:
        return []
    links = []
    for provider in ("telegram", "threads", "pinterest"):
        if not getattr(user, f"has_{provider}_integration") or not getattr(user, f"{provider}_show_on_{placement}"):
            continue
        profile = public_profile(provider, getattr(user, f"{provider}_public_profile"))
        if not profile:
            continue
        username = profile.get("username")
        url = None
        if username:
            bases = {"telegram": "https://t.me/", "threads": "https://www.threads.com/@",
                     "pinterest": "https://www.pinterest.com/"}
            url = bases[provider] + username
        links.append({"icon": f"bi-{provider}", "provider": provider.title(), "url": url,
                      "label": profile.get("title") or f"@{username}"})
    return links
