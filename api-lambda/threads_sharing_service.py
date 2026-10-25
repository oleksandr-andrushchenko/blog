"""Owner-authorized Threads account connections and article publishing."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as URLRequest, urlopen

from botocore.exceptions import ClientError

from api_route_metadata import API_URL_ROUTES
from app_config import get_config
from article_sharing import authorize_article_owner, article_media, remember_article_share
from cryptography.fernet import Fernet, InvalidToken
from query_dtos import ArticleStatus, UserStatus
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, find_category,
    get_dynamodb_item, get_article_url, html_to_text, jinja2_env, utc_now,
    logger,
)
from web import HTTPException

INTEGRATION_KEY = "INTEGRATION#threads"
PENDING_KEY = "INTEGRATION#threads#pending"
COOKIE_NAME = "threads_connection"
CONNECT_LIFETIME_MS = 10 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000
GRAPH_URL = "https://graph.threads.com"
PUBLISH_TIMEOUT_SECONDS = 15
CONFIRM_TIMEOUT_SECONDS = 3
MEDIA_CHECK_INTERVAL_MS = 60 * 1000
MEDIA_PROCESSING_LIFETIME_MS = 5 * 60 * 1000


class ThreadsRequestUnconfirmed(HTTPException):
    """The remote result is unknown; a publishing request must not be repeated."""


class ThreadsConnectionError(HTTPException):
    def __init__(self, error, step):
        super().__init__(error.status_code, error.detail)
        self.connection_step = step


def _connection_step(step, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except HTTPException as error:
        raise ThreadsConnectionError(error, step) from None


def _authorize(user, actor):
    if user.id != actor.id:
        raise HTTPException(403, "Only the account owner can manage integrations")
    if actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, "An active account is required")


def _cipher():
    try:
        return Fernet((get_config().get("threads_token_encryption_key") or "").encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "Threads connections are not available yet. Please try again later") from None


def _settings():
    if not get_config().get("threads_app_id") or not get_config().get("threads_app_secret"):
        raise HTTPException(503, "Threads connections are not available yet. Please try again later")
    for name in ("api_base_url", "web_base_url"):
        url = urlsplit(get_config().get(name) or "")
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise HTTPException(503, "Threads connections are not available yet. Please try again later")
    _cipher()
    return get_config()


def callback_url():
    return _settings()["api_base_url"].rstrip("/") + API_URL_ROUTES["threads-callback"]


def threads_call(path, *, method="GET", token=None, timeout=8, **params):
    """Use urllib to avoid HTTP client request logs containing OAuth credentials.

    Do not retry publishing: a timeout can follow a successful remote write.
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    url = GRAPH_URL + path
    data = None
    if method == "GET":
        if params:
            url += "?" + urlencode(params)
    else:
        data = urlencode(params).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = URLRequest(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            details = json.load(error).get("error", {})
            code = details.get("code")
            subcode = details.get("error_subcode")
        except (ValueError, AttributeError):
            code = subcode = None
        # Never log provider messages, request URLs, or bodies: Meta can echo
        # authorization codes and tokens in them. Numeric codes identify the failure.
        code = code if type(code) is int else None
        subcode = subcode if type(subcode) is int else None
        logger.warning("Threads API request failed: operation=%s http_status=%s provider_code=%s provider_subcode=%s",
                       path, error.code, code, subcode)
        if error.code == 429 or code in (4, 32, 613):
            raise HTTPException(429, "Threads rate limit reached. Wait before trying again") from None
        if error.code in (401, 403) or code in (10, 190, 200):
            raise HTTPException(409,
                                "Reconnect Threads in your profile's Integrations tab and allow posting access") from None
        if error.code in (400, 404):
            raise HTTPException(422, "Threads rejected the request. Reconnect your account and try again") from None
        raise ThreadsRequestUnconfirmed(502, "Threads is unavailable. Check your Threads profile before retrying a share") from None
    except (URLError, TimeoutError, ValueError) as error:
        logger.warning("Threads API request failed: operation=%s failure=%s", path, type(error).__name__)
        raise ThreadsRequestUnconfirmed(502,
                                       "Threads did not confirm the request. Check your Threads profile before retrying a share") from None
    if not isinstance(result, dict) or result.get("error"):
        logger.warning("Threads API request failed: operation=%s failure=invalid_response", path)
        raise ThreadsRequestUnconfirmed(502, "Threads did not confirm the request")
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
            conflict = "ConditionalCheckFailed" in str(error)
        if conflict:
            raise HTTPException(409,
                                "This connection attempt expired or was replaced. Connect Threads again from your profile") from None
        raise


def _guard(operation, record):
    if record:
        operation["ConditionExpression"] = "session_hash = :session"
        operation["ExpressionAttributeValues"] = {":session": record["session_hash"]}
    else:
        operation["ConditionExpression"] = "attribute_not_exists(pk)"


def _set_flag(writes, user_id, connected):
    add_dynamodb_update_transact(writes, (f"USER#{user_id}", "META"),
                                 changes={"has_threads_integration": connected}, add_updated_at=False)
    writes[-1]["Update"]["ConditionExpression"] = "attribute_exists(pk)"


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
    connected = bool(connection and connection.get("app_id") == settings["threads_app_id"] and
                     connection.get("expires_at", 0) > utc_now())
    pending = _read(user.id, PENDING_KEY)
    result = {"configured": True, "connected": connected,
              "pending": bool(pending and pending["expires_at"] > utc_now()),
              "reconnect_required": bool(connection and not connected)}
    if connected:
        result["username"] = connection["username"]
    return result


def begin_connection(user, actor):
    _authorize(user, actor)
    settings = _settings()
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = utc_now()
    pending = {"session_hash": hashlib.sha256(state.encode()).hexdigest(),
               "binding_hash": hashlib.sha256(binding.encode()).hexdigest(),
               "expires_at": now + CONNECT_LIFETIME_MS, "created_at": now}
    writes = []
    add_dynamodb_put_transact(writes, (f"USER#{user.id}", PENDING_KEY), pending)
    _write(writes)
    url = "https://www.threads.com/oauth/authorize?" + urlencode({
        "client_id": settings["threads_app_id"], "redirect_uri": callback_url(),
        "scope": "threads_basic,threads_content_publish", "response_type": "code", "state": state,
    })
    return {"connect_url": url, "expires_at": pending["expires_at"]}, binding


def _token_result(result):
    token = result.get("access_token")
    lifetime = result.get("expires_in")
    if not isinstance(token, str) or not token or not isinstance(lifetime, int) or lifetime <= 0:
        raise HTTPException(502, "Threads did not confirm access. Connect your account again")
    return token, lifetime


def complete_connection(actor, *, state, binding, code, denied=False):
    _authorize(actor, actor)
    settings = _settings()
    if not state or not binding or len(state) > 128 or len(binding) > 128:
        raise HTTPException(409, "Connection link expired. Connect Threads again from your profile")
    pending = _read(actor.id, PENDING_KEY)
    now = utc_now()
    digest = hashlib.sha256(state.encode()).hexdigest()
    binding_hash = hashlib.sha256(binding.encode()).hexdigest()
    if (not pending or pending["expires_at"] <= now or pending.get("claimed") or
            not hmac.compare_digest(pending["session_hash"], digest) or
            not hmac.compare_digest(pending["binding_hash"], binding_hash)):
        raise HTTPException(409, "Connection link expired. Connect Threads again from your profile")
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
        raise HTTPException(422, "Threads did not authorize the connection. Connect again from your profile")
    short = _connection_step("authorization", threads_call, "/oauth/access_token", method="POST",
                             client_id=settings["threads_app_id"], client_secret=settings["threads_app_secret"],
                             grant_type="authorization_code", redirect_uri=callback_url(), code=code)
    if not isinstance(short.get("access_token"), str) or not short["access_token"]:
        raise ThreadsConnectionError(HTTPException(502, "Threads did not confirm access. Connect your account again"),
                                     "authorization")
    long_lived = _connection_step("token", threads_call, "/access_token", access_token=short["access_token"],
                                 grant_type="th_exchange_token", client_secret=settings["threads_app_secret"])
    token, lifetime = _connection_step("token", _token_result, long_lived)
    profile = _connection_step("profile", threads_call, "/v1.0/me", token=token, fields="id,username")
    if (not profile.get("id") or not profile.get("username") or
            (short.get("user_id") and str(short["user_id"]) != str(profile["id"]))):
        raise ThreadsConnectionError(HTTPException(502, "Threads did not confirm your profile. Connect your account again"),
                                     "profile")
    now = utc_now()
    identity = {"user_id": actor.id, "threads_user_id": str(profile["id"]),
                "app_id": settings["threads_app_id"], "access_token": token}
    connection = {"session_hash": digest, "app_id": settings["threads_app_id"],
                  "threads_user_id": identity["threads_user_id"], "username": profile["username"],
                  "encrypted_token": _cipher().encrypt(json.dumps(identity).encode()).decode(),
                  "expires_at": now + lifetime * 1000, "refreshed_at": now, "created_at": now}
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
        raise HTTPException(409, "Connect Threads in your profile's Integrations tab first")
    if connection.get("app_id") != settings["threads_app_id"] or connection.get("expires_at", 0) <= utc_now():
        raise HTTPException(409, "Your Threads connection expired. Reconnect in your profile's Integrations tab")
    try:
        identity = json.loads(_cipher().decrypt(connection["encrypted_token"].encode()))
        if (identity["user_id"] != actor.id or identity["app_id"] != settings["threads_app_id"] or
                identity["threads_user_id"] != connection["threads_user_id"]):
            raise ValueError("Connection identity mismatch")
        token = identity["access_token"]
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise HTTPException(409, "Reconnect Threads in your profile's Integrations tab") from None
    # Long-lived tokens can be refreshed after 24 hours; active sharing keeps them usable.
    if utc_now() - connection["refreshed_at"] >= DAY_MS:
        token, lifetime = _token_result(
            threads_call("/refresh_access_token", access_token=token, grant_type="th_refresh_token"))
        identity["access_token"] = token
        refreshed = {**connection, "refreshed_at": utc_now(), "expires_at": utc_now() + lifetime * 1000,
                     "encrypted_token": _cipher().encrypt(json.dumps(identity).encode()).decode()}
        writes = []
        add_dynamodb_put_transact(writes, (f"USER#{actor.id}", INTEGRATION_KEY), refreshed)
        operation = writes[-1]["Put"]
        _guard(operation, connection)
        operation["ConditionExpression"] += " AND encrypted_token = :previous"
        operation["ExpressionAttributeValues"][":previous"] = connection["encrypted_token"]
        _write(writes)
    return token, connection


def _confirm_recent_post(token, text, started_at):
    """Read back one bounded page; never retry the write or accept an older post."""
    try:
        result = threads_call("/v1.0/me/threads", token=token, fields="id,text,timestamp", limit=5,
                              timeout=CONFIRM_TIMEOUT_SECONDS)
    except HTTPException:
        return None
    posts = result.get("data")
    if not isinstance(posts, list):
        return None
    for post in posts:
        if not isinstance(post, dict) or not post.get("id") or post.get("text") != text:
            continue
        timestamp = post.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        try:
            published = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if published.tzinfo is None:
                continue
            # Provider timestamps have second precision; match this attempt's second or later.
            if started_at // 1000 <= int(published.timestamp()) <= utc_now() // 1000 + 1:
                return post
        except (ValueError, OverflowError, OSError):
            continue
    return None


def share_article(article, actor, request, *, continuation=None):
    _authorize(actor, actor)
    authorize_article_owner(article, actor)
    if article.status != ArticleStatus.PUBLISHED:
        raise HTTPException(404, "Only published articles can be shared to Threads")
    token, connection = _access_token(actor)
    if continuation is not None:
        state = _decode_media_share(continuation, article, actor, connection)
        return _remember_share(article, _advance_media_share(state, token, connection), token,
                               get_article_url(request, article, absolute=True), actor, request)
    media = article_media(article, request)
    if any(item['format'] not in ('image', 'video') for item in media):
        raise HTTPException(422, 'Threads supports image and video attachments only. This article contains audio or text files')
    url = get_article_url(request, article, absolute=True)
    category = find_category(article.category) if article.category else None
    context = {"category_name": category.name if category else (article.category or "other").replace("-", " ").title(),
               "title": article.title, "is_premium": article.is_premium, "article_url": url,
               "profile_url": "https://www.threads.com/@" + connection["username"]}
    # A .txt template deliberately emits plain text, including literal ampersands in URLs.
    template = jinja2_env().get_template("threads/article-post.txt")
    description = html_to_text(article.preview) or ""
    text = template.render(context, description=description).strip()
    if len(text) > 500:
        fixed = template.render(context, description="").strip()
        available = 500 - len(fixed) - 3  # Description separator and ellipsis.
        if available < 0:
            raise HTTPException(422, "Article details are too long to share to Threads")
        description = description[:available] + "…"
        text = template.render(context, description=description).strip()
    current = _read(actor.id, INTEGRATION_KEY)
    if not current or current["session_hash"] != connection["session_hash"]:
        raise HTTPException(409, "Your Threads connection changed. Reload the page and try again")
    if media:
        state = {'user_id': actor.id, 'article_id': article.id, 'session_hash': connection['session_hash'],
                 'media': media, 'text': text, 'url': url, 'index': 0, 'children': [],
                 'expires_at': utc_now() + 60 * 60 * 1000}
        return _remember_share(article, _advance_media_share(state, token, connection), token, url, actor, request)
    started_at = utc_now()
    try:
        result = threads_call("/v1.0/me/threads", method="POST", token=token, timeout=PUBLISH_TIMEOUT_SECONDS,
                              media_type="TEXT", text=text, link_attachment=url, auto_publish_text="true")
    except ThreadsRequestUnconfirmed:
        result = None
    if not result or not result.get("id"):
        result = _confirm_recent_post(token, text, started_at)
    if not result:
        return {"shared": False, "status": "unconfirmed", "username": connection["username"]}
    return _remember_share(article, {
        "shared": True, "post_id": str(result["id"]), "username": connection["username"],
    }, token, url, actor, request)


def _post_permalink(post_id, token):
    try:
        result = threads_call(f'/v1.0/{post_id}', token=token, fields='permalink',
                              timeout=CONFIRM_TIMEOUT_SECONDS)
        permalink = result.get('permalink')
        parts = urlsplit(permalink) if isinstance(permalink, str) else None
        if (parts and parts.scheme == 'https' and not parts.query and not parts.fragment
                and parts.netloc in ('www.threads.com', 'threads.com', 'www.threads.net', 'threads.net')):
            return permalink
    except Exception as error:
        logger.warning('Threads permalink lookup failed: post_id=%s failure=%s', post_id, type(error).__name__)
    return None


def _remember_share(article, result, token, article_url, actor, request):
    if result.get('shared') is not True:
        return result
    return remember_article_share(article, 'threads', result, actor=actor, request=request, article_url=article_url,
                                 post_url=_post_permalink(result['post_id'], token))


def _decode_media_share(continuation, article, actor, connection):
    """Only resume server-issued media work for this account and connection."""
    try:
        state = json.loads(_cipher().decrypt(continuation.encode(), ttl=3600))
        if (state['user_id'] != actor.id or state['article_id'] != article.id or
                state['session_hash'] != connection['session_hash'] or state['expires_at'] <= utc_now()):
            raise ValueError('Share identity mismatch')
    except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(409, 'This share expired or your connection changed. Check your Threads profile before sharing again') from None
    return state


def _media_progress(state, connection, *, retry_after=0):
    return {'shared': False, 'status': 'processing', 'username': connection['username'],
            'continuation': _cipher().encrypt(json.dumps(state).encode()).decode(), 'retry_after': retry_after}


def _advance_media_share(state, token, connection):
    """Advance at most one media write per HTTP request, avoiding gateway timeouts.

    The browser carries encrypted progress and waits between processing checks. No
    access tokens are included, and an uncertain publish is never repeated.
    """
    try:
        return _advance_media_share_step(state, token, connection)
    except HTTPException:
        if state.get('root_id'):
            # An earlier carousel is already public; retrying the whole share would duplicate it.
            return {'shared': False, 'status': 'unconfirmed', 'username': connection['username']}
        raise


def _advance_media_share_step(state, token, connection):
    waiting = state.setdefault('waiting', [])
    if (not state.get('pending') and waiting and
            (state['index'] == len(state['media']) or len(state['children']) + len(waiting) == 20)):
        state['pending'] = waiting.pop(0)
    pending = state.get('pending')
    if pending:
        if utc_now() < pending['check_at']:
            return _media_progress(state, connection, retry_after=(pending['check_at'] - utc_now() + 999) // 1000)
        result = threads_call('/v1.0/' + pending['id'], token=token, fields='id,status')
        status = result.get('status')
        if status == 'IN_PROGRESS':
            if utc_now() - pending['created_at'] >= MEDIA_PROCESSING_LIFETIME_MS:
                raise HTTPException(422, 'Threads could not finish processing the attachments. Check the media files before trying again')
            pending['check_at'] = utc_now() + MEDIA_CHECK_INTERVAL_MS
            return _media_progress(state, connection, retry_after=60)
        if status == 'PUBLISHED':
            # A replay or a lost publish response must never publish this container again.
            return {'shared': False, 'status': 'unconfirmed', 'username': connection['username']}
        if status != 'FINISHED':
            raise HTTPException(422, 'Threads could not process an attachment. Check its format and size before trying again')
        state.pop('pending')
        if pending['kind'] == 'child':
            state['children'].append(pending['id'])
            if waiting:
                return _media_progress(state, connection)
        else:
            started_at = utc_now()
            try:
                post = threads_call('/v1.0/me/threads_publish', method='POST', token=token,
                                    timeout=PUBLISH_TIMEOUT_SECONDS, creation_id=pending['id'])
            except ThreadsRequestUnconfirmed:
                post = None
            if not post or not post.get('id'):
                post = _confirm_recent_post(token, state['post_text'], started_at)
            if not post:
                return {'shared': False, 'status': 'unconfirmed', 'username': connection['username']}
            state.setdefault('root_id', str(post['id']))
            if state['index'] >= len(state['media']):
                return {'shared': True, 'post_id': state['root_id'], 'username': connection['username']}
            state['reply_to_id'] = str(post['id'])
            state['children'] = []
            return _media_progress(state, connection)

    remaining = len(state['media']) - state['index']
    batch_size = min(20, remaining + len(state['children']) + len(waiting))
    state['post_text'] = state['text'] if not state.get('root_id') else 'More examples\n' + state['url']
    params = {}
    if batch_size > 1 and len(state['children']) == batch_size:
        params.update(media_type='CAROUSEL', children=','.join(state['children']), text=state['post_text'])
        kind = 'parent'
    else:
        item = state['media'][state['index']]
        params.update(media_type=item['format'].upper(), **{item['format'] + '_url': item['url']})
        kind = 'child' if batch_size > 1 else 'parent'
        if kind == 'child':
            params['is_carousel_item'] = 'true'
        else:
            params['text'] = state['post_text']
        state['index'] += 1
    if kind == 'parent' and state.get('reply_to_id'):
        params['reply_to_id'] = state['reply_to_id']
    result = threads_call('/v1.0/me/threads', method='POST', token=token, **params)
    container_id = result.get('id')
    if not isinstance(container_id, str) or not container_id.isdigit():
        raise HTTPException(502, 'Threads did not confirm the media upload. Check your profile before trying again')
    pending = {'id': container_id, 'kind': kind, 'created_at': utc_now(), 'check_at': utc_now() + 30000}
    if kind == 'child':
        waiting.append(pending)
        return _media_progress(state, connection)
    state['pending'] = pending
    return _media_progress(state, connection, retry_after=30)
