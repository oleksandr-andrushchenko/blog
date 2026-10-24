"""Sitemap worker uses the API bundle without an HTTP request."""
from functools import lru_cache


@lru_cache
def get_web_request():
    """Return a request backed by the web route registry for background sitemap generation."""
    from web import Application, Request
    from web_route_metadata import WEB_URL_ROUTES

    app = Application()
    for name, path in WEB_URL_ROUTES.items():
        app.add_url_route(path, name)

    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "root_path": "",
        "query_string": b"",
        "scheme": "https",
        "server": ("localhost", 443),
        "client": ("127.0.0.1", 0),
        "headers": [],
        "app": app,
    })


def handler(event, context):
    from sitemap_service import run_sitemap_event
    return run_sitemap_event(event)
