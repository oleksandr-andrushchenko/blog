from unittest.mock import patch

import shared_utils
from web import Application, Request
from web_route_metadata import WEB_URL_ROUTES


def request_with_static_route():
    app = Application()
    app.add_url_route(WEB_URL_ROUTES["static-file"], "static-file")
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "root_path": "",
        "query_string": b"",
        "scheme": "https",
        "server": ("example.com", 443),
        "client": ("192.0.2.10", 12345),
        "headers": [(b"host", b"example.com")],
        "app": app,
    })


def test_static_base_url_is_used_for_relative_and_absolute_requests():
    request = request_with_static_route()
    with patch.dict(shared_utils.config, {"static_base_url": "https://static.example.com"}):
        expected = "https://static.example.com/styles.css?_=42"
        assert shared_utils.get_static_url(request, "styles.css", _=42) == expected
        assert shared_utils.get_static_url(request, "styles.css", absolute=True, _=42) == expected
