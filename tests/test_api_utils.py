import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
sys.path.insert(0, str(project_root / "shared"))
sys.path.insert(0, str(project_root / "api-lambda"))

import api_utils
import app_config
import search_engine_submissions


IMAGE_FILENAME = "61677cb2-b588-45b8-8036-240e185a382f_1200x518.png"


@pytest.mark.parametrize("source", [IMAGE_FILENAME, f"/{IMAGE_FILENAME}"])
def test_find_static_image_filename_accepts_relative_static_urls(monkeypatch, source):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")

    assert api_utils.find_static_image_filename(f'<img alt="Example" src="{source}">') == IMAGE_FILENAME


def test_find_static_image_filename_accepts_configured_static_origin(monkeypatch):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")

    content = f'<img src="https://static.example.com/{IMAGE_FILENAME}" alt="Example">'

    assert api_utils.find_static_image_filename(content) == IMAGE_FILENAME


@pytest.mark.parametrize("source", [
    f"https://images.example.com/{IMAGE_FILENAME}",
    f"http://static.example.com/{IMAGE_FILENAME}",
    f"data:image/png;base64,{IMAGE_FILENAME}",
    f"/nested/{IMAGE_FILENAME}",
])
def test_find_static_image_filename_rejects_non_static_urls(monkeypatch, source):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")

    assert api_utils.find_static_image_filename(f'<img src="{source}">') is None


def test_find_static_image_filename_skips_external_image_before_static_image(monkeypatch):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")
    content = (
        f'<img src="https://images.example.com/{IMAGE_FILENAME}">'
        f'<img src="https://static.example.com/{IMAGE_FILENAME}">'
    )

    assert api_utils.find_static_image_filename(content) == IMAGE_FILENAME


def test_notify_search_engines_submits_urls_to_indexnow(monkeypatch):
    requests = []

    class Client:
        def __init__(self, timeout):
            assert timeout == 5.0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            requests.append((url, json))
            return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(search_engine_submissions, "is_prod", lambda: True)
    monkeypatch.setattr(search_engine_submissions, "get_indexnow_key", lambda: "test-indexnow-key")
    monkeypatch.setattr(search_engine_submissions, "get_web_base_url", lambda: "https://example.com")
    monkeypatch.setattr(search_engine_submissions.httpx, "Client", Client)

    assert search_engine_submissions.notify_search_engines([
        "https://example.com/article-one",
        "https://example.com/article-one",
        "https://example.com/article-two",
    ])
    assert requests == [("https://api.indexnow.org/indexnow", {
        "host": "example.com",
        "key": "test-indexnow-key",
        "keyLocation": "https://example.com/test-indexnow-key.txt",
        "urlList": [
            "https://example.com/article-one",
            "https://example.com/article-two",
        ],
    })]


def test_notify_search_engines_skips_non_production(monkeypatch):
    monkeypatch.setattr(search_engine_submissions, "is_prod", lambda: False)

    assert not search_engine_submissions.notify_search_engines("https://example.com/article")


def test_notify_search_engines_rejects_foreign_urls(monkeypatch):
    monkeypatch.setattr(search_engine_submissions, "is_prod", lambda: True)
    monkeypatch.setattr(search_engine_submissions, "get_indexnow_key", lambda: "test-indexnow-key")
    monkeypatch.setattr(search_engine_submissions, "get_web_base_url", lambda: "https://example.com")

    with pytest.raises(ValueError, match="must belong to example.com"):
        search_engine_submissions.notify_search_engines("https://other.example/article")


def test_get_indexnow_key_rejects_invalid_configuration(monkeypatch):
    monkeypatch.setattr(app_config, "config", {"indexnow_key": "too short"})

    with pytest.raises(ValueError, match="INDEXNOW_KEY"):
        app_config.get_indexnow_key()


def test_sitemap_submission_uses_configured_engine_apis(monkeypatch):
    requests = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, timeout):
            assert timeout == 10.0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def put(self, url, **kwargs):
            requests.append(("PUT", url, kwargs))
            return Response()

        def post(self, url, **kwargs):
            requests.append(("POST", url, kwargs))
            return Response()

    monkeypatch.setattr(search_engine_submissions, "is_prod", lambda: True)
    monkeypatch.setattr(search_engine_submissions.httpx, "Client", Client)
    monkeypatch.setattr(
        search_engine_submissions,
        "get_google_search_console_credentials_secret_arn",
        lambda: "credentials-secret-arn",
    )
    monkeypatch.setattr(search_engine_submissions, "get_google_search_console_site_url", lambda: "sc-domain:example.com")
    monkeypatch.setattr(search_engine_submissions, "_get_google_access_token", lambda value: "google-token")
    monkeypatch.setattr(search_engine_submissions, "get_bing_webmaster_api_key", lambda: "bing-key")
    monkeypatch.setattr(search_engine_submissions, "get_web_base_url", lambda: "https://example.com")
    monkeypatch.setattr(search_engine_submissions, "get_yandex_webmaster_oauth_token", lambda: "yandex-token")
    monkeypatch.setattr(search_engine_submissions, "get_yandex_webmaster_user_id", lambda: "42")
    monkeypatch.setattr(search_engine_submissions, "get_yandex_webmaster_host_id", lambda: "https:example.com:443")

    result = search_engine_submissions.submit_sitemap_to_search_engines(
        "https://static.example.com/sitemap.xml"
    )

    assert result == {"Google": True, "Bing": True, "Yandex": True}
    assert requests == [
        (
            "PUT",
            "https://www.googleapis.com/webmasters/v3/sites/"
            "sc-domain%3Aexample.com/sitemaps/https%3A%2F%2Fstatic.example.com%2Fsitemap.xml",
            {"headers": {"Authorization": "Bearer google-token"}},
        ),
        (
            "POST",
            "https://ssl.bing.com/webmaster/api.svc/json/SubmitFeed",
            {
                "params": {"apikey": "bing-key"},
                "json": {
                    "siteUrl": "https://example.com",
                    "feedUrl": "https://static.example.com/sitemap.xml",
                },
            },
        ),
        (
            "POST",
            "https://api.webmaster.yandex.net/v4/user/42/hosts/"
            "https%3Aexample.com%3A443/user-added-sitemaps",
            {
                "headers": {"Authorization": "OAuth yandex-token"},
                "json": {"url": "https://static.example.com/sitemap.xml"},
            },
        ),
    ]


def test_sitemap_submission_skips_unconfigured_engines(monkeypatch):
    monkeypatch.setattr(search_engine_submissions, "is_prod", lambda: True)
    monkeypatch.setattr(search_engine_submissions, "get_google_search_console_credentials_secret_arn", lambda: "")
    monkeypatch.setattr(search_engine_submissions, "get_bing_webmaster_api_key", lambda: "")
    monkeypatch.setattr(search_engine_submissions, "get_yandex_webmaster_oauth_token", lambda: "")
    monkeypatch.setattr(search_engine_submissions, "get_yandex_webmaster_user_id", lambda: "")
    monkeypatch.setattr(search_engine_submissions, "get_yandex_webmaster_host_id", lambda: "")

    assert search_engine_submissions.submit_sitemap_to_search_engines(
        "https://static.example.com/sitemap.xml"
    ) == {}
