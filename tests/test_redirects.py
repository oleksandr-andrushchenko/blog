"""Trailing-slash redirects preserve requests and only strip slashes for real routes."""
import asyncio
import os
from pathlib import Path
import sys
import unittest

import httpx

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(project_root / "shared"))
from web import Application, TrailingSlashMiddleware, Request, Response
from lambda_adapter import _invoke


class TrailingSlashRedirectTests(unittest.TestCase):
    def setUp(self):
        self.app = Application()
        self.app.add_middleware(TrailingSlashMiddleware)
        self.calls = []

        @self.app.get("/", name="home")
        @self.app.get("/articles", name="articles")
        @self.app.get("/@{slug}", name="profile")
        @self.app.get("/@{slug}/{article_slug}", name="article")
        async def page(request: Request):
            self.calls.append(request.url.path)
            return Response("OK")

        @self.app.post("/submit", name="submit")
        async def submit(request: Request):
            body = await request.body()
            self.calls.append(body)
            return Response(body)

        self.app.add_url_route("/metadata-only", "metadata-only")

    def request(self, url, method="GET", follow_redirects=False, **kwargs):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)) as client:
                return await client.request(method, url, follow_redirects=follow_redirects, **kwargs)
        return asyncio.run(run())

    def test_trailing_slash_redirects_preserve_origin_and_query(self):
        query = "tag=a%2Fb&tag=c+d&empty="
        for scheme in ("http", "https"):
            for host in ("example.com", "www.example.com"):
                for suffix in ("", "/", "///", "%2F"):
                    with self.subTest(scheme=scheme, host=host, suffix=suffix):
                        response = self.request(f"{scheme}://{host}/articles{suffix}?{query}",
                                                follow_redirects=True)
                        self.assertEqual(response.status_code, 200)
                        changed = bool(suffix)
                        self.assertEqual(len(response.history), int(changed))
                        if changed:
                            self.assertEqual(response.history[0].status_code, 308)
                        self.assertEqual(str(response.url), f"{scheme}://{host}/articles?{query}")

    def test_unknown_routes_and_wrong_methods_do_not_redirect(self):
        for path, method, expected in [("/missing/", "GET", 404),
                                       ("/metadata-only/", "GET", 404),
                                       ("/submit/", "GET", 404),
                                       ("/articles/", "POST", 404)]:
            with self.subTest(path=path, method=method):
                response = self.request("https://example.com" + path, method)
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("location", response.headers)
        self.assertEqual(self.calls, [])

    def test_post_redirect_preserves_method_and_body(self):
        response = self.request("http://www.example.com/submit/?a=1", "POST",
                                content=b"original body", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"original body")
        self.assertEqual(self.calls, [b"original body"])
        self.assertEqual(len(response.history), 1)
        self.assertEqual(response.history[0].status_code, 308)

    def test_local_http_and_origin_hosts_do_not_get_rewritten(self):
        for host in ("localhost:5000", "127.0.0.1:5000", "example.execute-api.amazonaws.com"):
            with self.subTest(host=host):
                response = self.request(f"http://{host}/articles/", follow_redirects=True)
                self.assertEqual(str(response.url), f"http://{host}/articles")
                self.assertEqual(len(response.history), 1)
                self.assertEqual(response.history[0].headers["location"], "/articles")

    def test_root_and_encoded_profile_paths(self):
        response = self.request("https://example.com/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)
        response = self.request("https://example.com/%40j-doe/?q=%26")
        self.assertEqual(response.headers["location"], "/%40j-doe?q=%26")
        self.assertEqual(response.status_code, 308)

    def test_forwarded_host_is_not_used_for_redirects(self):
        response = self.request("https://example.com/articles/", headers={
            "x-forwarded-host": "attacker.example", "x-forwarded-proto": "http",
        })
        self.assertEqual(response.headers["location"], "/articles")

    def test_lambda_trailing_slash_redirect_preserves_profile_marker(self):
        response = asyncio.run(_invoke(self.app, {
            "rawPath": "/@j-doe/article/",
            "headers": {"host": "example.com"},
            "requestContext": {"http": {"method": "GET"}},
        }))

        self.assertEqual(response["statusCode"], 308)
        self.assertEqual(response["headers"]["location"], "/@j-doe/article")
