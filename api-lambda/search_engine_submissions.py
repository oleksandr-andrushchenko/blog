import json
from collections.abc import Iterable
from functools import lru_cache
from urllib.parse import quote, urlparse

import httpx

from app_config import (
    get_bing_webmaster_api_key,
    get_google_search_console_credentials_secret_arn,
    get_google_search_console_site_url,
    get_indexnow_key,
    get_web_base_url,
    get_yandex_webmaster_host_id,
    get_yandex_webmaster_oauth_token,
    get_yandex_webmaster_user_id,
    is_prod,
)
from shared_utils import logger


def notify_search_engines(urls: str | Iterable[str]) -> bool:
    """Notify IndexNow participants about added or updated public URLs."""
    if not is_prod():
        return False

    key = get_indexnow_key()
    if not key:
        logger.warning("Search engine notification skipped: INDEXNOW_KEY is not configured")
        return False

    if isinstance(urls, str):
        urls = [urls]
    urls = list(dict.fromkeys(urls))
    if not urls:
        return False

    site = urlparse(get_web_base_url())
    if not site.scheme or not site.netloc:
        raise ValueError("WEB_BASE_URL must be an absolute URL")
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != site.netloc:
            raise ValueError(f"IndexNow URL must belong to {site.netloc}: {url}")

    key_location = f"{site.scheme}://{site.netloc}/{key}.txt"
    with httpx.Client(timeout=5.0) as client:
        for start in range(0, len(urls), 10_000):
            response = client.post("https://api.indexnow.org/indexnow", json={
                "host": site.netloc,
                "key": key,
                "keyLocation": key_location,
                "urlList": urls[start:start + 10_000],
            })
            response.raise_for_status()
    return True


@lru_cache
def _get_google_service_account_info(secret_arn: str) -> dict:
    import boto3

    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def _get_google_access_token(credentials_secret_arn: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    credentials = Credentials.from_service_account_info(
        _get_google_service_account_info(credentials_secret_arn),
        scopes=["https://www.googleapis.com/auth/webmasters"],
    )
    credentials.refresh(Request())
    return credentials.token


def _submit_google_sitemap(client: httpx.Client, sitemap_url: str) -> bool:
    credentials_secret_arn = get_google_search_console_credentials_secret_arn()
    if not credentials_secret_arn:
        return False
    site_url = get_google_search_console_site_url()
    access_token = _get_google_access_token(credentials_secret_arn)
    endpoint = (
        "https://www.googleapis.com/webmasters/v3/sites/"
        f"{quote(site_url, safe='')}/sitemaps/{quote(sitemap_url, safe='')}"
    )
    response = client.put(endpoint, headers={"Authorization": f"Bearer {access_token}"})
    response.raise_for_status()
    return True


def _submit_bing_sitemap(client: httpx.Client, sitemap_url: str) -> bool:
    api_key = get_bing_webmaster_api_key()
    if not api_key:
        return False
    response = client.post(
        "https://ssl.bing.com/webmaster/api.svc/json/SubmitFeed",
        params={"apikey": api_key},
        json={"siteUrl": get_web_base_url(), "feedUrl": sitemap_url},
    )
    response.raise_for_status()
    return True


def _submit_yandex_sitemap(client: httpx.Client, sitemap_url: str) -> bool:
    token = get_yandex_webmaster_oauth_token()
    user_id = get_yandex_webmaster_user_id()
    host_id = get_yandex_webmaster_host_id()
    if not all((token, user_id, host_id)):
        return False
    endpoint = (
        f"https://api.webmaster.yandex.net/v4/user/{quote(user_id, safe='')}"
        f"/hosts/{quote(host_id, safe='')}/user-added-sitemaps"
    )
    response = client.post(
        endpoint,
        headers={"Authorization": f"OAuth {token}"},
        json={"url": sitemap_url},
    )
    if response.status_code != 409:  # The sitemap was already registered.
        response.raise_for_status()
    return True


def submit_sitemap_to_search_engines(sitemap_url: str) -> dict[str, bool]:
    """Submit a sitemap through each configured webmaster API independently."""
    if not is_prod():
        return {}

    results = {}
    with httpx.Client(timeout=10.0) as client:
        for engine, submit in (
            ("Google", _submit_google_sitemap),
            ("Bing", _submit_bing_sitemap),
            ("Yandex", _submit_yandex_sitemap),
        ):
            try:
                if submit(client, sitemap_url):
                    results[engine] = True
            except Exception as exc:
                results[engine] = False
                logger.warning("%s sitemap submission failed: %s", engine, exc)
    return results
