"""Shared Telegram bot onboarding and owner-authorized channel publishing."""
import hashlib
import hmac
import json
import re
import secrets
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, urlopen

from botocore.exceptions import ClientError

from api_route_metadata import API_URL_ROUTES
from app_config import get_config
from article_sharing import authorize_article_owner, article_media, remember_article_share
from query_dtos import ArticleStatus, UserStatus
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, build_dynamodb_delete_item_params, dynamodb_transact_write, find_category, find_user,
    get_dynamodb_item, get_article_url, html_to_text, jinja2_env, utc_now,
)
from web import HTTPException, jsonable

INTEGRATION_KEY = "INTEGRATION#telegram"
PENDING_KEY = "INTEGRATION#telegram#pending"
CONNECT_LIFETIME_MS = 10 * 60 * 1000


def authorize_owner(user, actor):
    if user.id != actor.id:
        raise HTTPException(403, "Only the account owner can manage integrations")
    if actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, "An active account is required")


def _bot_token():
    token = get_config().get("telegram_publisher_bot_token") or ""
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise HTTPException(503, "Telegram connections are not configured yet")
    return token


def _bot_id():
    return int(_bot_token().split(":", 1)[0])


def _webhook_secret():
    secret = get_config().get("telegram_publisher_webhook_secret") or ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
        raise HTTPException(503, "Telegram connections are not configured yet")
    return secret


def verify_webhook(secret):
    if not hmac.compare_digest(_webhook_secret().encode(), (secret or "").encode()):
        raise HTTPException(403, "Invalid Telegram webhook authentication")


def telegram_call(method, *, files=None, **payload):
    # urllib avoids HTTP client INFO request logs exposing bot tokens embedded in URLs.
    content_type = 'application/json'
    data = json.dumps(jsonable(payload)).encode()
    if files:
        boundary = secrets.token_hex(24)
        content_type = 'multipart/form-data; boundary=' + boundary
        parts = []
        for name, value in jsonable(payload).items():
            value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                         + value.encode() + b'\r\n')
        for name, (filename, content) in files.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                         'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b'\r\n')
        data = b''.join(parts) + f'--{boundary}--\r\n'.encode()
    request = URLRequest(
        f"https://api.telegram.org/bot{_bot_token()}/{method}",
        data=data, headers={"Content-Type": content_type}, method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:
            result = json.load(response)
    except HTTPError as error:
        if error.code == 429:
            raise HTTPException(429, "Telegram rate limit reached. Wait before trying again") from None
        if error.code in (400, 401, 403, 404):
            raise HTTPException(422,
                                "Telegram rejected the request. Reconnect your channel and check posting permissions") from None
        raise HTTPException(502, "Telegram is unavailable. Try again later") from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(502,
                            "Telegram did not confirm the request. Check your channel before retrying a share") from None
    if not isinstance(result, dict) or not result.get("ok") or "result" not in result:
        raise HTTPException(502, "Telegram did not confirm the request")
    return result["result"]


def _read(pk, sk):
    return get_dynamodb_item(pk, sk, consistent_read=True)


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
            raise HTTPException(409, "This connection attempt expired or was replaced. Connect again from your profile") from None
        raise


def _guard(operation, session_hash, now=None):
    operation["ConditionExpression"] = "session_hash = :session"
    operation["ExpressionAttributeValues"] = {":session": session_hash}
    if now is not None:
        operation["ConditionExpression"] += " AND expires_at > :now"
        operation["ExpressionAttributeValues"][":now"] = now


def _session_key(session_hash):
    return f"TELEGRAM_CONNECT#{session_hash}", "META"


def _picker_key(telegram_user_id, request_id):
    return f"TELEGRAM_PICKER#{telegram_user_id}", str(request_id)


def _remove_session(writes, pending):
    if not pending:
        return
    session_key = _session_key(pending["session_hash"])
    session = _read(*session_key)
    add_dynamodb_delete_transact(writes, session_key)
    if session and session.get("telegram_user_id"):
        add_dynamodb_delete_transact(writes, _picker_key(session["telegram_user_id"], session["request_id"]))


def get_connection(user, actor):
    authorize_owner(user, actor)
    connection = _read(f"USER#{user.id}", INTEGRATION_KEY)
    # Previous personal-bot connections require the new shared-bot authorization flow.
    if not connection or not connection.get("telegram_user_id"):
        return None
    if not get_config().get("telegram_publisher_bot_token") or connection.get("bot_id") != _bot_id():
        return None
    return connection


def connection_status(user, actor):
    connection = get_connection(user, actor)
    configured = bool(get_config().get("telegram_publisher_bot_token") and
                      get_config().get("telegram_publisher_webhook_secret"))
    result = {"connected": bool(connection), "configured": configured}
    if connection:
        result.update({key: connection.get(key) for key in (
            "channel_id", "channel_title", "channel_username", "bot_username", "verified_at")})
    pending = _read(f"USER#{user.id}", PENDING_KEY)
    result["pending"] = bool(pending and pending["expires_at"] > utc_now())
    return jsonable(result)


def begin_connection(user, actor):
    authorize_owner(user, actor)
    _webhook_secret()
    bot = telegram_call("getMe")
    nonce = secrets.token_urlsafe(32)
    digest = hashlib.sha256(nonce.encode()).hexdigest()
    now = utc_now()
    session = {"user_id": user.id, "session_hash": digest, "request_id": secrets.randbelow(2**31),
               "bot_id": bot["id"], "bot_username": bot["username"],
               "expires_at": now + CONNECT_LIFETIME_MS, "created_at": now}
    old = _read(f"USER#{user.id}", PENDING_KEY)
    writes = []
    _remove_session(writes, old)
    add_dynamodb_put_transact(writes, _session_key(digest), session)
    add_dynamodb_put_transact(writes, (f"USER#{user.id}", PENDING_KEY), {
        "session_hash": digest, "expires_at": session["expires_at"], "created_at": now,
    })
    if old:
        _guard(writes[-1]["Put"], old["session_hash"])
    else:
        writes[-1]["Put"]["ConditionExpression"] = "attribute_not_exists(pk)"
    _write(writes)
    return {"connect_url": f"https://t.me/{bot['username']}?start={nonce}",
            "expires_at": session["expires_at"]}


def _set_connection_flag(writes, user_id, connected):
    add_dynamodb_update_transact(writes, (f"USER#{user_id}", "META"),
                                 changes={"has_telegram_integration": connected}, add_updated_at=False)
    writes[-1]["Update"]["ConditionExpression"] = "attribute_exists(pk)"


def disconnect(user, actor):
    authorize_owner(user, actor)
    pending = _read(f"USER#{user.id}", PENDING_KEY)
    writes = []
    _remove_session(writes, pending)
    add_dynamodb_delete_transact(writes, (f"USER#{user.id}", PENDING_KEY))
    if pending:
        _guard(writes[-1]["Delete"], pending["session_hash"])
    else:
        writes[-1]["Delete"]["ConditionExpression"] = "attribute_not_exists(pk)"
    add_dynamodb_delete_transact(writes, (f"USER#{user.id}", INTEGRATION_KEY))
    _set_connection_flag(writes, user.id, False)
    _write(writes)
    return {"connected": False, "pending": False}


def _active_session(session):
    if not session or session["expires_at"] <= utc_now() or session["bot_id"] != _bot_id():
        raise HTTPException(409, "Connection link expired. Connect again from your profile")
    pending = _read(f"USER#{session['user_id']}", PENDING_KEY)
    if not pending or pending["session_hash"] != session["session_hash"]:
        raise HTTPException(409, "Connection link was cancelled. Connect again from your profile")
    user = find_user(session["user_id"])
    if not user or user.status != UserStatus.ACTIVE:
        raise HTTPException(403, "An active account is required")
    return user


def _admin_rights():
    # Bot API requires the basic boolean fields even when only posting is requested.
    return {"is_anonymous": False, "can_manage_chat": True, "can_delete_messages": False,
            "can_manage_video_chats": False, "can_restrict_members": False,
            "can_promote_members": False, "can_change_info": False, "can_invite_users": False,
            "can_post_stories": False, "can_edit_stories": False, "can_delete_stories": False,
            "can_post_messages": True}


def _start_connection(message, nonce):
    session_hash = hashlib.sha256(nonce.encode()).hexdigest()
    session = _read(*_session_key(session_hash))
    _active_session(session)
    telegram_user_id = message["from"]["id"]
    if session.get("telegram_user_id") not in (None, telegram_user_id):
        raise HTTPException(409, "This link has already been used by another Telegram account")
    session = {**session, "telegram_user_id": telegram_user_id}
    writes = []
    add_dynamodb_put_transact(writes, _session_key(session_hash), session)
    _guard(writes[-1]["Put"], session_hash, utc_now())
    writes[-1]["Put"]["ConditionExpression"] += " AND (attribute_not_exists(telegram_user_id) OR telegram_user_id = :actor)"
    writes[-1]["Put"]["ExpressionAttributeValues"][":actor"] = telegram_user_id
    add_dynamodb_put_transact(writes, _picker_key(telegram_user_id, session["request_id"]), session)
    check = build_dynamodb_delete_item_params((f"USER#{session['user_id']}", PENDING_KEY))["Delete"]
    _guard(check, session_hash, utc_now())
    writes.append({"ConditionCheck": check})
    _write(writes)
    rights = _admin_rights()
    telegram_call("sendMessage", chat_id=telegram_user_id,
                  text="Choose the channel you want to connect, then allow this bot to post messages. Nothing will be posted automatically.",
                  reply_markup={"keyboard": [[{"text": "Choose channel", "request_chat": {
                      "request_id": session["request_id"], "chat_is_channel": True,
                      "user_administrator_rights": {**rights, "can_promote_members": True},
                      "bot_administrator_rights": rights,
                      "request_title": True, "request_username": True,
                  }}]], "resize_keyboard": True, "one_time_keyboard": True})


def _verify_posting(channel_id, telegram_user_id, bot_id):
    for member_id in (telegram_user_id, bot_id):
        member = telegram_call("getChatMember", chat_id=channel_id, user_id=member_id)
        if member.get("status") == "creator":
            continue
        if member.get("status") != "administrator" or not member.get("can_post_messages"):
            raise HTTPException(422, "Both you and the bot need permission to post in the channel. Reconnect from your profile")


def _finish_connection(message):
    telegram_user_id = message["from"]["id"]
    shared = message["chat_shared"]
    session = _read(*_picker_key(telegram_user_id, shared.get("request_id")))
    if not session:
        return  # Already consumed, or an unsolicited channel selection.
    _active_session(session)
    channel = telegram_call("getChat", chat_id=shared["chat_id"])
    if channel.get("type") != "channel":
        raise HTTPException(422, "Select a Telegram channel")
    _verify_posting(channel["id"], telegram_user_id, session["bot_id"])
    record = {"channel_id": str(channel["id"]), "channel_title": channel.get("title", ""),
              "channel_username": channel.get("username"), "telegram_user_id": telegram_user_id,
              "bot_id": session["bot_id"], "bot_username": session["bot_username"],
              "verified_at": utc_now()}
    writes = []
    add_dynamodb_put_transact(writes, (f"USER#{session['user_id']}", INTEGRATION_KEY), record)
    add_dynamodb_delete_transact(writes, (f"USER#{session['user_id']}", PENDING_KEY))
    _guard(writes[-1]["Delete"], session["session_hash"], utc_now())
    add_dynamodb_delete_transact(writes, _session_key(session["session_hash"]))
    add_dynamodb_delete_transact(writes, _picker_key(telegram_user_id, session["request_id"]))
    _guard(writes[-1]["Delete"], session["session_hash"])
    _set_connection_flag(writes, session["user_id"], True)
    _write(writes)
    # Finish the durable connection before this optional acknowledgement. Telegram may retry delivery.
    telegram_call("sendMessage", chat_id=telegram_user_id,
                  text="Channel connected. Return to the site to share an article to your channel.",
                  reply_markup={"remove_keyboard": True})


def handle_update(update):
    message = update.get("message") or {}
    if not isinstance(message, dict):
        return {"ok": True}
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return {"ok": True}
    if chat.get("type") != "private" or sender.get("is_bot") or not sender.get("id") or chat.get("id") != sender["id"]:
        return {"ok": True}
    try:
        text = message.get("text", "")
        if not isinstance(text, str):
            return {"ok": True}
        start = re.fullmatch(r"/start(?:@[A-Za-z0-9_]+)? ([A-Za-z0-9_-]{43})", text)
        if start:
            _start_connection(message, start[1])
        elif isinstance(message.get("chat_shared"), dict):
            _finish_connection(message)
        elif text.startswith("/start"):
            telegram_call("sendMessage", chat_id=sender["id"],
                          text="Open Integrations in your site profile and select Connect Telegram to link your channel.")
    except HTTPException as error:
        if error.status_code not in (403, 409, 422):
            raise  # Provider outages/rate limits should be retried by Telegram.
        telegram_call("sendMessage", chat_id=sender["id"], text=error.detail)
    return {"ok": True}


class _TelegramTextCounter(HTMLParser):
    """Count visible UTF-16 units, excluding markup and decoding entities once."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.units = 0

    def handle_data(self, data):
        self.units += len(data.encode("utf-16-le")) // 2


def _telegram_text_units(text):
    counter = _TelegramTextCounter()
    counter.feed(str(text))
    counter.close()
    return counter.units


def share_article(article, actor, request):
    authorize_owner(actor, actor)
    authorize_article_owner(article, actor)
    if article.status != ArticleStatus.PUBLISHED:
        raise HTTPException(404, "Only published articles can be shared to a channel")
    connection = get_connection(actor, actor)
    if not connection:
        raise HTTPException(409, "Connect a Telegram channel in your profile's Integrations tab first")
    _verify_posting(connection["channel_id"], connection["telegram_user_id"], connection["bot_id"])
    media = article_media(article, request)
    url = get_article_url(request, article, absolute=True)
    category = find_category(article.category) if article.category else None
    category_name = category.name if category else (article.category or "other").replace("-", " ").title()
    template = jinja2_env().get_template("telegram/article-post.html")
    context = {
        "category_slug": article.category, "category_name": category_name,
        "title": article.title, "article_url": url,
        "channel_url": f"https://t.me/{connection['channel_username']}" if connection.get("channel_username") else None,
    }
    description = html_to_text(article.preview) or ""
    text = template.render(context, description=description)
    limit = 1024 if media else 4096
    if _telegram_text_units(text) > limit:
        # Reserve the rendered heading, title, CTA and link; only shorten the description.
        fixed_units = _telegram_text_units(template.render(context, description=""))
        available = limit - fixed_units - 3  # Description paragraph separator and ellipsis.
        if available < 0:
            raise HTTPException(422, "Article details are too long to share to Telegram")
        description = description.encode("utf-16-le")[:available * 2].decode("utf-16-le", errors="ignore") + "…"
        text = template.render(context, description=description)
    if media:
        message = _send_article_media(connection['channel_id'], media, text)
    else:
        message = telegram_call("sendMessage", chat_id=connection["channel_id"], text=text, parse_mode="HTML",
                                link_preview_options={"url": url})
    return remember_article_share(article, 'telegram', {
        "shared": True, "message_id": message["message_id"], "channel_title": connection["channel_title"],
    }, actor=actor, request=request, article_url=url, post_url=_message_url(message, connection),
       event_id=f"{connection['channel_id']}:{message['message_id']}")


def _message_url(message, connection):
    chat = message.get('chat') or {}
    username = chat.get('username') if chat.get('id') else connection.get('channel_username')
    if username:
        return f'https://t.me/{username}/{message["message_id"]}'
    channel_id = str(chat.get('id') or connection['channel_id'])
    if channel_id.startswith('-100'):
        return f'https://t.me/c/{channel_id[4:]}/{message["message_id"]}'
    return None


def _send_article_media(channel_id, media, caption):
    return telegram_call('sendPhoto', chat_id=channel_id, photo=media[0]['url'],
                         caption=caption, parse_mode='HTML')


def configure_webhook(*, apply=False):
    """Review or register the shared bot webhook without exposing its credentials."""
    _bot_token()
    secret = _webhook_secret()
    base = (get_config().get("api_base_url") or "").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API_BASE_URL must be a public HTTPS application URL")
    payload = {"url": base + API_URL_ROUTES["telegram-webhook"],
               "allowed_updates": ["message"], "max_connections": 2}
    if apply:
        telegram_call("setWebhook", **payload, secret_token=secret)
    return {"method": "setWebhook", **payload, "secret_token": "[configured; redacted]", "applied": apply}
