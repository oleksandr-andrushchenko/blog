"""API-owned LinkedIn OAuth connections and explicit article preview publishing."""
import hashlib
import hmac
import json
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request as URLRequest, build_opener

from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, InvalidToken

from api_route_metadata import API_URL_ROUTES
from app_config import get_config
from article_sharing import authorize_article_owner, remember_article_share
from query_dtos import ArticleStatus, UserStatus
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, find_category,
    get_dynamodb_item, get_article_url, html_to_text, utc_now,
)
from web import HTTPException

INTEGRATION_KEY = "INTEGRATION#linkedin"
PENDING_KEY = "INTEGRATION#linkedin#pending"
COOKIE_NAME = "linkedin_connection"
CONNECT_LIFETIME_MS = 10 * 60 * 1000


class LinkedInRequestUnconfirmed(HTTPException):
    """A write may have succeeded. Never automatically repeat it."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _authorize(user, actor):
    if user.id != actor.id:
        raise HTTPException(403, "Only the account owner can manage integrations")
    if actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, "An active account is required")


def _cipher():
    try:
        return Fernet((get_config().get("linkedin_token_encryption_key") or "").encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "LinkedIn connections are not available yet. Please try again later") from None


def _settings():
    if not get_config().get("linkedin_app_id") or not get_config().get("linkedin_app_secret"):
        raise HTTPException(503, "LinkedIn connections are not available yet. Please try again later")
    for name in ("api_base_url", "web_base_url"):
        url = urlsplit(get_config().get(name) or "")
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise HTTPException(503, "LinkedIn connections are not available yet. Please try again later")
    _cipher()
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", get_config().get("linkedin_api_version") or ""):
        raise HTTPException(503, "LinkedIn connections are not available yet. Please try again later")
    return get_config()


def callback_url():
    return _settings()["api_base_url"].rstrip("/") + API_URL_ROUTES["linkedin-callback"]


def linkedin_call(path, *, token=None, form=None, payload=None):
    """Never retry writes or expose credentials through provider errors or redirects."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if path.startswith("/rest/"):
        headers.update({"LinkedIn-Version": get_config()["linkedin_api_version"],
                        "X-Restli-Protocol-Version": "2.0.0"})
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    host = "https://www.linkedin.com" if form is not None else "https://api.linkedin.com"
    request = URLRequest(host + path, data=data, headers=headers,
                         method="POST" if data is not None else "GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            if path == "/rest/posts":
                post_id = response.headers.get("x-restli-id", "")
                if response.status != 201 or not re.fullmatch(r"urn:li:(share|ugcPost):\d+", post_id):
                    raise LinkedInRequestUnconfirmed(502, "LinkedIn did not confirm the post")
                return {"id": post_id}
            result = json.load(response)
    except HTTPError as error:
        if error.code in (401, 403):
            raise HTTPException(409, "Reconnect LinkedIn and allow profile and posting access") from None
        if error.code == 429:
            raise HTTPException(429, "LinkedIn rate limit reached. Wait before trying again") from None
        if error.code in (400, 404, 413, 422):
            raise HTTPException(422, "LinkedIn rejected the request. Check your connection and article") from None
        raise LinkedInRequestUnconfirmed(502, "LinkedIn did not confirm the request") from None
    except (URLError, OSError, ValueError):
        raise LinkedInRequestUnconfirmed(502, "LinkedIn did not confirm the request") from None
    if not isinstance(result, dict) or result.get("error"):
        raise LinkedInRequestUnconfirmed(502, "LinkedIn did not confirm the request")
    return result


def _read(user_id, key):
    return get_dynamodb_item(f"USER#{user_id}", key, consistent_read=True)


def _write(writes):
    try:
        dynamodb_transact_write(writes)
    except (DynamoDBTransactionError, ClientError) as error:
        if isinstance(error, ClientError):
            response = error.response
            reasons = {reason.get("Code") for reason in response.get("CancellationReasons", [])}
            conflict = (response.get("Error", {}).get("Code") == "TransactionCanceledException" and
                        "ConditionalCheckFailed" in reasons and reasons <= {None, "None", "ConditionalCheckFailed"})
        else:
            conflict = error.is_conditional()
        if conflict:
            raise HTTPException(409,
                                "This connection attempt expired or was replaced. Connect LinkedIn again from your profile") from None
        raise


def _guard(operation, record):
    if record:
        operation["ConditionExpression"] = "session_hash = :session"
        operation["ExpressionAttributeValues"] = {":session": record["session_hash"]}
    else:
        operation["ConditionExpression"] = "attribute_not_exists(pk)"


def _set_flag(writes, user_id, connected):
    add_dynamodb_update_transact(writes, (f"USER#{user_id}", "META"),
                                 changes={"has_linkedin_integration": connected}, add_updated_at=False)
    operation = writes[-1]["Update"]
    operation["ConditionExpression"] = "attribute_exists(pk) AND (attribute_not_exists(#status) OR #status = :active)"
    operation["ExpressionAttributeNames"]["#status"] = "status"
    operation["ExpressionAttributeValues"][":active"] = "active"


def connection_status(user, actor):
    _authorize(user, actor)
    try:
        settings = _settings()
    except HTTPException:
        result = {"configured": False, "connected": False, "pending": False}
        if _read(user.id, INTEGRATION_KEY) or _read(user.id, PENDING_KEY):
            result["reconnect_required"] = True
        return result
    connection = _read(user.id, INTEGRATION_KEY)
    connected = bool(connection and connection.get("app_id") == settings["linkedin_app_id"] and
                     connection.get("expires_at", 0) > utc_now())
    pending = _read(user.id, PENDING_KEY)
    result = {"configured": True, "connected": connected,
              "pending": bool(pending and pending["expires_at"] > utc_now()),
              "reconnect_required": bool(connection and not connected)}
    if connected:
        try:
            _access_token(actor)
        except HTTPException:
            result.update({"connected": False, "reconnect_required": True})
        else:
            result["username"] = connection["username"]
    return result


def begin_connection(user, actor, request):
    _authorize(user, actor)
    settings = _settings()
    auth_cookie = request.cookies.get("token")
    if not auth_cookie:
        raise HTTPException(401, "Sign in again before connecting LinkedIn")
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = utc_now()
    pending = {"session_hash": hashlib.sha256(state.encode()).hexdigest(),
               "binding_hash": hashlib.sha256(binding.encode()).hexdigest(),
               "auth_hash": hashlib.sha256(auth_cookie.encode()).hexdigest(),
               "expires_at": now + CONNECT_LIFETIME_MS, "created_at": now}
    writes = []
    add_dynamodb_put_transact(writes, (f"USER#{user.id}", PENDING_KEY), pending)
    _write(writes)
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "client_id": settings["linkedin_app_id"], "redirect_uri": callback_url(),
        "scope": "openid profile w_member_social", "response_type": "code", "state": state,
    })
    return {"connect_url": url, "expires_at": pending["expires_at"]}, binding


def _token_result(result):
    token = result.get("access_token")
    lifetime = result.get("expires_in")
    if (not isinstance(token, str) or not token or len(token) > 16384 or
            any(ord(char) < 33 or ord(char) > 126 for char in token) or
            type(lifetime) is not int or lifetime <= 0):
        raise HTTPException(502, "LinkedIn did not confirm access. Connect your account again")
    return token, lifetime


def complete_connection(actor, *, state, binding, code, auth_cookie, denied=False):
    _authorize(actor, actor)
    settings = _settings()
    if not state or not binding or not auth_cookie or len(state) > 128 or len(binding) > 128:
        raise HTTPException(409, "Connection link expired. Connect LinkedIn again from your profile")
    pending = _read(actor.id, PENDING_KEY)
    now = utc_now()
    digest = hashlib.sha256(state.encode()).hexdigest()
    binding_hash = hashlib.sha256(binding.encode()).hexdigest()
    if (not pending or pending["expires_at"] <= now or pending.get("claimed") or
            not hmac.compare_digest(pending["session_hash"], digest) or
            not hmac.compare_digest(pending["binding_hash"], binding_hash) or
            not hmac.compare_digest(pending["auth_hash"], hashlib.sha256(auth_cookie.encode()).hexdigest())):
        raise HTTPException(409, "Connection link expired. Connect LinkedIn again from your profile")
    # Claim once before exchanging a single-use authorization code. The pending record
    # remains until completion so replacement/disconnect can cancel an in-flight exchange.
    writes = []
    add_dynamodb_put_transact(writes, (f"USER#{actor.id}", PENDING_KEY), {**pending, "claimed": True})
    operation = writes[-1]["Put"]
    _guard(operation, pending)
    operation["ConditionExpression"] += " AND expires_at > :now AND attribute_not_exists(claimed)"
    operation["ExpressionAttributeValues"][":now"] = now
    _write(writes)
    if denied:
        writes = []
        add_dynamodb_delete_transact(writes, (f"USER#{actor.id}", PENDING_KEY))
        _guard(writes[-1]["Delete"], pending)
        _write(writes)
        return False
    if not code or len(code) > 4096:
        raise HTTPException(422, "LinkedIn did not authorize the connection. Connect again from your profile")
    result = linkedin_call("/oauth/v2/accessToken", form={
        "client_id": settings["linkedin_app_id"], "client_secret": settings["linkedin_app_secret"],
        "grant_type": "authorization_code", "redirect_uri": callback_url(), "code": code})
    token, lifetime = _token_result(result)
    scope = result.get("scope")
    if scope is not None and (not isinstance(scope, str) or
                              not {"openid", "profile", "w_member_social"} <= set(scope.replace(",", " ").split())):
        raise HTTPException(409, "Reconnect LinkedIn and allow profile and posting access")
    profile = linkedin_call("/v2/userinfo", token=token)
    if (not isinstance(profile.get("sub"), str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", profile["sub"]) or
            not isinstance(profile.get("name"), str) or not profile["name"].strip()):
        raise HTTPException(502, "LinkedIn did not confirm your profile. Connect again")
    now = utc_now()
    identity = {"user_id": actor.id, "linkedin_user_id": profile["sub"],
                "app_id": settings["linkedin_app_id"], "access_token": token}
    connection = {"session_hash": digest, "app_id": settings["linkedin_app_id"],
                  "linkedin_user_id": identity["linkedin_user_id"], "username": profile["name"],
                  "encrypted_token": _cipher().encrypt(json.dumps(identity).encode()).decode(),
                  "expires_at": now + lifetime * 1000, "created_at": now}
    writes = []
    add_dynamodb_delete_transact(writes, (f"USER#{actor.id}", PENDING_KEY))
    operation = writes[-1]["Delete"]
    _guard(operation, pending)
    operation["ConditionExpression"] += " AND expires_at > :now AND claimed = :claimed"
    operation["ExpressionAttributeValues"].update({":now": now, ":claimed": True})
    add_dynamodb_put_transact(writes, (f"USER#{actor.id}", INTEGRATION_KEY), connection)
    _set_flag(writes, actor.id, True)
    _write(writes)
    return True


def disconnect(user, actor):
    _authorize(user, actor)
    writes = []
    for key in (PENDING_KEY, INTEGRATION_KEY):
        record = _read(user.id, key)
        add_dynamodb_delete_transact(writes, (f"USER#{user.id}", key))
        _guard(writes[-1]["Delete"], record)
    _set_flag(writes, user.id, False)
    _write(writes)
    return {"connected": False, "pending": False}


def _access_token(actor):
    settings = _settings()
    connection = _read(actor.id, INTEGRATION_KEY)
    if not connection:
        raise HTTPException(409, "Connect LinkedIn in your profile's Integrations tab first")
    if connection.get("app_id") != settings["linkedin_app_id"] or connection.get("expires_at", 0) <= utc_now():
        raise HTTPException(409, "Your LinkedIn connection expired. Reconnect in your profile's Integrations tab")
    try:
        identity = json.loads(_cipher().decrypt(connection["encrypted_token"].encode()))
        if (identity["user_id"] != actor.id or identity["app_id"] != settings["linkedin_app_id"] or
                identity["linkedin_user_id"] != connection["linkedin_user_id"]):
            raise ValueError("Connection identity mismatch")
        token = identity["access_token"]
    except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(409, "Reconnect LinkedIn in your profile's Integrations tab") from None
    return token, connection


def share_article(article, actor, request):
    _authorize(actor, actor)
    authorize_article_owner(article, actor)
    if article.status != ArticleStatus.PUBLISHED:
        raise HTTPException(404, "Only published articles can be shared to LinkedIn")
    token, connection = _access_token(actor)
    url = get_article_url(request, article, absolute=True)
    category = find_category(article.category) if article.category else None
    category_name = category.name if category else (article.category or "Other")
    title = article.title[:399]
    preview = html_to_text(article.preview) or ""
    # Link posts carry only the public preview. Premium/full article bodies stay local.
    fixed = f"{title}\n{category_name}\n\nRead the full article on SysDesPro:\n{url}"
    available = 2800 - len(fixed) - 2
    if available < 0:
        raise HTTPException(422, "Article details are too long to share to LinkedIn")
    preview = preview if len(preview) <= available else preview[:max(0, available - 1)] + "…"
    # Escape LinkedIn little-text syntax so titles/previews cannot introduce mentions.
    def commentary_text(description):
        text = f"{title}\n{category_name}\n\n{description}\n\nRead the full article on SysDesPro:\n{url}"
        return re.sub(r"([\\|{}@\[\]()<>#*_~])", r"\\\1", text)

    commentary = commentary_text(preview)
    while len(commentary.encode("utf-16-le")) // 2 > 3000 and preview:
        preview = preview[:max(0, len(preview) - 100)]
        commentary = commentary_text(preview)
    if len(commentary.encode("utf-16-le")) // 2 > 3000:
        raise HTTPException(422, "Article details are too long to share to LinkedIn")
    payload = {"author": "urn:li:person:" + connection["linkedin_user_id"],
               "commentary": commentary, "visibility": "PUBLIC",
               "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                "thirdPartyDistributionChannels": []},
               "content": {"article": {"source": url, "title": title, "description": preview}},
               "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    current = _read(actor.id, INTEGRATION_KEY)
    if not current or current["session_hash"] != connection["session_hash"]:
        raise HTTPException(409, "Your LinkedIn connection changed. Reload before sharing")
    try:
        result = linkedin_call("/rest/posts", token=token, payload=payload)
    except LinkedInRequestUnconfirmed:
        return {"shared": False, "status": "unconfirmed"}
    post_id = result.get("id")
    if not isinstance(post_id, str) or not re.fullmatch(r"urn:li:(share|ugcPost):\d+", post_id):
        return {"shared": False, "status": "unconfirmed"}
    post_url = "https://www.linkedin.com/feed/update/" + post_id + "/"
    return remember_article_share(article, "linkedin", {"shared": True, "post_id": post_id, "post_url": post_url},
                                  actor=actor, request=request, article_url=url, post_url=post_url)
