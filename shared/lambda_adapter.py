"""Minimal API Gateway HTTP API (payload v2) to ASGI adapter."""

from __future__ import annotations

import asyncio
import base64
from urllib.parse import quote


def _scope(event: dict) -> dict:
    request_context = event.get("requestContext", {})
    http = request_context.get("http", {})
    event_headers = event.get("headers") or {}
    headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in event_headers.items()
    ]
    cookies = event.get("cookies") or []
    if cookies:
        headers.append((b"cookie", "; ".join(cookies).encode("latin-1")))

    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": http.get("protocol", "HTTP/1.1").removeprefix("HTTP/"),
        "method": http.get("method", event.get("httpMethod", "GET")),
        "scheme": headers and event_headers.get("x-forwarded-proto", "https") or "https",
        "path": event.get("rawPath", "/"),
        "raw_path": quote(event.get("rawPath", "/"), safe="/").encode(),
        "query_string": event.get("rawQueryString", "").encode(),
        "headers": headers,
        "server": (event_headers.get("host", "lambda"), 443),
        "client": (http.get("sourceIp", ""), 0),
        "root_path": "",
    }


async def _invoke(app, event: dict) -> dict:
    body = event.get("body") or ""
    body_bytes = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
    request_sent = False
    status = 500
    response_headers: list[tuple[bytes, bytes]] = []
    response_body = bytearray()

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    async def send(message):
        nonlocal status, response_headers
        if message["type"] == "http.response.start":
            status = message["status"]
            response_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            response_body.extend(message.get("body", b""))

    await app(_scope(event), receive, send)

    headers: dict[str, str] = {}
    cookies: list[str] = []
    for raw_name, raw_value in response_headers:
        name = raw_name.decode("latin-1")
        value = raw_value.decode("latin-1")
        if name.lower() == "set-cookie":
            cookies.append(value)
        else:
            headers[name] = value

    try:
        encoded_body = response_body.decode("utf-8")
        is_base64 = False
    except UnicodeDecodeError:
        encoded_body = base64.b64encode(response_body).decode()
        is_base64 = True

    result = {
        "statusCode": status,
        "headers": headers,
        "body": encoded_body,
        "isBase64Encoded": is_base64,
    }
    if cookies:
        result["cookies"] = cookies
    return result


def make_handler(app):
    def handler(event, context):
        return asyncio.run(_invoke(app, event))

    return handler
