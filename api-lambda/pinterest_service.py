"""Owner-bound Pinterest OAuth, board selection, and explicit image Pin publishing."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as URLRequest, urlopen

from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, InvalidToken

from account_write_guards import add_account_guard
from api_route_metadata import API_URL_ROUTES
from app_config import config
from integration_profiles import public_profile
from article_sharing import article_media, remember_article_share
from query_dtos import ArticleStatus, UserStatus
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, get_dynamodb_item,
    get_article_url, html_to_text, logger, utc_now,
)
from web import HTTPException

INTEGRATION_KEY = "INTEGRATION#pinterest"
PENDING_KEY = "INTEGRATION#pinterest#pending"
COOKIE_NAME = "pinterest_connection"
CONNECT_LIFETIME_MS = 10 * 60 * 1000
API_BASE = "https://api.pinterest.com/v5"


class PinterestRequestUnconfirmed(HTTPException):
    """A remote write may have succeeded; never automatically repeat it."""


def _authorize(user, actor):
    if user.id != actor.id:
        raise HTTPException(403, "Only the account owner can manage integrations")
    if actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, "An active account is required")


def _cipher():
    try:
        return Fernet((config.get("pinterest_token_encryption_key") or "").encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "Pinterest connections are not available yet. Please try again later") from None


def _settings():
    if not config.get("pinterest_app_id") or not config.get("pinterest_app_secret"):
        raise HTTPException(503, "Pinterest connections are not available yet. Please try again later")
    for name in ("api_base_url", "web_base_url"):
        url = urlsplit(config.get(name) or "")
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise HTTPException(503, "Pinterest connections are not available yet. Please try again later")
    _cipher()
    return config


def callback_url():
    return _settings()["api_base_url"].rstrip("/") + API_URL_ROUTES["pinterest-callback"]


def pinterest_call(path, *, method="GET", token=None, form=None, payload=None, **params):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if form is not None:
        credentials = (config["pinterest_app_id"] + ":" + config["pinterest_app_secret"]).encode()
        headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urlencode(form).encode()
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    else:
        body = None
    url = API_BASE + path + ("?" + urlencode(params) if params else "")
    try:
        with urlopen(URLRequest(url, data=body, headers=headers, method=method), timeout=15) as response:
            result = json.load(response)
    except HTTPError as error:
        # Provider bodies can echo secrets: expose only a recovery message and status.
        if error.code == 429:
            raise HTTPException(429, "Pinterest rate limit reached. Wait before trying again") from None
        if error.code in (401, 403):
            raise HTTPException(409, "Reconnect Pinterest and allow access to your account and boards") from None
        if error.code in (400, 404):
            raise HTTPException(422, "Pinterest rejected the request. Check your account and selected board") from None
        raise PinterestRequestUnconfirmed(502, "Pinterest did not confirm the request. Check your profile before retrying") from None
    except (URLError, TimeoutError, ValueError):
        raise PinterestRequestUnconfirmed(502, "Pinterest did not confirm the request. Check your profile before retrying") from None
    if not isinstance(result, dict):
        raise PinterestRequestUnconfirmed(502, "Pinterest returned an incomplete response. Check your profile before retrying")
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
                                "This connection attempt expired or was replaced. Connect Pinterest again from your profile") from None
        raise


def _guard(operation, record):
    if record:
        operation["ConditionExpression"] = "session_hash = :session"
        operation["ExpressionAttributeValues"] = {":session": record["session_hash"]}
    else:
        operation["ConditionExpression"] = "attribute_not_exists(pk)"


def _set_flag(writes, user_id, connected, connection=None):
    profile = public_profile("pinterest", connection) if connection else None
    add_dynamodb_update_transact(writes, (f"USER#{user_id}", "META"),
                                 changes={"has_pinterest_integration": connected,
                                          "pinterest_public_profile": profile}, add_updated_at=False)
    writes[-1]["Update"]["ConditionExpression"] = "attribute_exists(pk) AND attribute_not_exists(deletion_id)"


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
    connected = bool(connection and connection.get("app_id") == settings["pinterest_app_id"] and
                     max(connection.get("expires_at", 0), connection.get("refresh_expires_at", 0)) > utc_now())
    pending = _read(user.id, PENDING_KEY)
    result = {"configured": True, "connected": connected,
              "pending": bool(pending and pending["expires_at"] > utc_now()),
              "reconnect_required": bool(connection and not connected)}
    if connected:
        result["username"] = connection["username"]
        result["board_id"] = connection.get("board_id")
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
    add_account_guard(writes, user.id)
    _write(writes)
    url = "https://www.pinterest.com/oauth/?" + urlencode({
        "client_id": settings["pinterest_app_id"], "redirect_uri": callback_url(),
        "scope": "user_accounts:read,boards:read,pins:write", "response_type": "code", "state": state,
    })
    return {"connect_url": url, "expires_at": pending["expires_at"]}, binding


def _token_identity(result, actor, profile):
    access, refresh = result.get('access_token'), result.get('refresh_token')
    lifetime, refresh_lifetime = result.get('expires_in'), result.get('refresh_token_expires_in')
    refresh_expires = result.get('refresh_token_expires_at')
    if type(refresh_expires) is int:
        refresh_lifetime = refresh_expires - utc_now() // 1000
    scope = result.get('scope')
    if not isinstance(scope, str) or not {'user_accounts:read', 'boards:read', 'pins:write'} <= set(scope.replace(',', ' ').split()):
        raise HTTPException(409, 'Reconnect Pinterest and allow account, board and Pin access')
    if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
            or type(lifetime) is not int or lifetime <= 0 or type(refresh_lifetime) is not int or refresh_lifetime <= 0):
        raise HTTPException(502, 'Pinterest did not confirm access. Connect again')
    identity = {'user_id': actor.id, 'app_id': config['pinterest_app_id'],
                'pinterest_user_id': str(profile['id']), 'access_token': access, 'refresh_token': refresh}
    now = utc_now()
    return {'encrypted_token': _cipher().encrypt(json.dumps(identity).encode()).decode(),
            'expires_at': now + lifetime * 1000, 'refresh_expires_at': now + refresh_lifetime * 1000}


def complete_connection(actor, *, state, binding, code, denied=False):
    _authorize(actor, actor)
    _settings()
    if not state or not binding or len(state) > 128 or len(binding) > 128:
        raise HTTPException(409, 'Connection link expired. Connect Pinterest again')
    pending = _read(actor.id, PENDING_KEY)
    digest = hashlib.sha256(state.encode()).hexdigest()
    if (not pending or pending['expires_at'] <= utc_now() or pending.get('claimed')
            or not hmac.compare_digest(pending['session_hash'], digest)
            or not hmac.compare_digest(pending['binding_hash'], hashlib.sha256(binding.encode()).hexdigest())):
        raise HTTPException(409, 'Connection link expired. Connect Pinterest again')
    writes = []
    add_dynamodb_put_transact(writes, (f'USER#{actor.id}', PENDING_KEY), {**pending, 'claimed': True})
    operation = writes[-1]['Put']
    _guard(operation, pending)
    operation['ConditionExpression'] += ' AND expires_at > :now AND attribute_not_exists(claimed)'
    operation['ExpressionAttributeValues'][':now'] = utc_now()
    add_account_guard(writes, actor.id)
    _write(writes)
    if denied:
        writes = []
        add_dynamodb_delete_transact(writes, (f'USER#{actor.id}', PENDING_KEY))
        _guard(writes[-1]['Delete'], pending)
        _write(writes)
        return False
    if not code or len(code) > 4096:
        raise HTTPException(422, 'Pinterest did not authorize the connection. Connect again')
    tokens = pinterest_call('/oauth/token', method='POST', form={
        'grant_type': 'authorization_code', 'code': code, 'redirect_uri': callback_url(),
    })
    access = tokens.get('access_token')
    if not isinstance(access, str) or not access:
        raise HTTPException(502, 'Pinterest did not confirm access. Connect again')
    profile = pinterest_call('/user_account', token=access)
    public = public_profile('pinterest', profile)
    if not str(profile.get('id', '')).isdigit() or not public:
        raise HTTPException(502, 'Pinterest did not confirm your account. Connect again')
    record = {'session_hash': digest, 'app_id': config['pinterest_app_id'],
              'pinterest_user_id': str(profile['id']), 'username': public['username'], 'created_at': utc_now(),
              **_token_identity(tokens, actor, profile)}
    writes = []
    add_dynamodb_delete_transact(writes, (f'USER#{actor.id}', PENDING_KEY))
    operation = writes[-1]['Delete']
    _guard(operation, pending)
    operation['ConditionExpression'] += ' AND claimed = :claimed AND expires_at > :now'
    operation['ExpressionAttributeValues'].update({':claimed': True, ':now': utc_now()})
    add_dynamodb_put_transact(writes, (f'USER#{actor.id}', INTEGRATION_KEY), record)
    _set_flag(writes, actor.id, True, record)
    operation = writes[-1]['Update']
    operation['ConditionExpression'] += ' AND (attribute_not_exists(#account_status) OR #account_status = :active)'
    operation['ExpressionAttributeNames']['#account_status'] = 'status'
    operation['ExpressionAttributeValues'][':active'] = 'active'
    _write(writes)
    return True


def disconnect(user, actor):
    _authorize(user, actor)
    writes = []
    for key in (PENDING_KEY, INTEGRATION_KEY):
        record = _read(user.id, key)
        add_dynamodb_delete_transact(writes, (f'USER#{user.id}', key))
        _guard(writes[-1]['Delete'], record)
    _set_flag(writes, user.id, False)
    _write(writes)
    return {'connected': False, 'pending': False}


def _access_token(actor):
    _authorize(actor, actor)
    _settings()
    connection = _read(actor.id, INTEGRATION_KEY)
    if not connection or connection.get('app_id') != config['pinterest_app_id']:
        raise HTTPException(409, 'Connect Pinterest in your profile first')
    try:
        identity = json.loads(_cipher().decrypt(connection['encrypted_token'].encode()))
        if (identity['user_id'] != actor.id or identity['app_id'] != config['pinterest_app_id']
                or identity['pinterest_user_id'] != connection['pinterest_user_id']):
            raise ValueError('Connection identity mismatch')
    except (InvalidToken, KeyError, ValueError, TypeError):
        raise HTTPException(409, 'Reconnect Pinterest in your profile') from None
    if connection['expires_at'] <= utc_now() + 300000:
        if connection.get('refresh_expires_at', 0) <= utc_now():
            raise HTTPException(409, 'Your Pinterest connection expired. Connect again')
        tokens = pinterest_call('/oauth/token', method='POST', form={
            'grant_type': 'refresh_token', 'refresh_token': identity['refresh_token'],
        })
        refreshed = {**connection, **_token_identity(tokens, actor, {'id': connection['pinterest_user_id']})}
        writes = []
        add_dynamodb_put_transact(writes, (f'USER#{actor.id}', INTEGRATION_KEY), refreshed)
        operation = writes[-1]['Put']
        _guard(operation, connection)
        operation['ConditionExpression'] += ' AND encrypted_token = :previous'
        operation['ExpressionAttributeValues'][':previous'] = connection['encrypted_token']
        add_account_guard(writes, actor.id)
        _write(writes)
        connection = refreshed
        identity = json.loads(_cipher().decrypt(connection['encrypted_token'].encode()))
    return identity['access_token'], connection


def _public_owned_board(board, connection):
    return (str(board.get('id', '')).isdigit() and board.get('privacy') == 'PUBLIC'
            and isinstance(board.get('owner'), dict)
            and board['owner'].get('username', '').casefold() == connection['username'].casefold())


def list_boards(user, actor):
    _authorize(user, actor)
    token, connection = _access_token(actor)
    boards, bookmark, seen = [], None, set()
    while True:
        params = {'page_size': 100}
        if bookmark:
            params['bookmark'] = bookmark
        response = pinterest_call('/boards', token=token, **params)
        items = response.get('items')
        if not isinstance(items, list):
            raise HTTPException(502, 'Could not load Pinterest boards. Try again later')
        for board in items:
            if isinstance(board, dict) and _public_owned_board(board, connection):
                boards.append({'id': str(board['id']), 'name': str(board.get('name') or 'Board')[:180]})
        bookmark = response.get('bookmark')
        if not bookmark:
            return {'boards': boards, 'board_id': connection.get('board_id')}
        if not isinstance(bookmark, str) or bookmark in seen or len(seen) >= 99:
            raise HTTPException(502, 'Could not finish loading Pinterest boards. Try again later')
        seen.add(bookmark)


def select_board(user, actor, board_id):
    _authorize(user, actor)
    if not isinstance(board_id, str) or not re.fullmatch(r'\d{1,30}', board_id):
        raise HTTPException(422, 'Choose a Pinterest board')
    token, connection = _access_token(actor)
    board = pinterest_call('/boards/' + board_id, token=token)
    if str(board.get('id')) != board_id or not _public_owned_board(board, connection):
        raise HTTPException(422, 'Choose a public board that you own')
    writes = []
    add_dynamodb_put_transact(writes, (f'USER#{user.id}', INTEGRATION_KEY), {**connection, 'board_id': board_id})
    operation = writes[-1]['Put']
    _guard(operation, connection)
    operation['ConditionExpression'] += ' AND encrypted_token = :previous'
    operation['ExpressionAttributeValues'][':previous'] = connection['encrypted_token']
    add_account_guard(writes, user.id)
    _write(writes)
    return {'saved': True, 'board_id': board_id}


def _pin_result(pin_id, username):
    return {'shared': True, 'post_id': pin_id, 'username': username,
            'post_url': f'https://www.pinterest.com/pin/{pin_id}/'}


def share_article(article, actor, request, event_id):
    _authorize(actor, actor)
    if article.owner_id != actor.id:
        raise HTTPException(403, 'Only the article owner can publish it to Pinterest')
    if article.status != ArticleStatus.PUBLISHED:
        raise HTTPException(404, 'Publish this article before sharing it')
    try:
        event_id = str(uuid.UUID(event_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, 'Invalid Pinterest sharing request') from None
    token, connection = _access_token(actor)
    if not connection.get('board_id'):
        raise HTTPException(409, 'Choose a Pinterest board in your profile’s Integrations tab first')
    media = next((item for item in article_media(article, request) if item['format'] == 'image'), None)
    if not media or urlsplit(media['url']).scheme != 'https':
        raise HTTPException(422, 'Pinterest needs a publicly accessible cover image. Add a cover to this article first')
    target = get_article_url(request, article, absolute=True)
    key = (f'USER#{actor.id}', 'PINTEREST_PUBLISH#' + event_id)
    previous = get_dynamodb_item(*key, consistent_read=True)
    if previous:
        if previous.get('article_id') != article.id or previous.get('session_hash') != connection['session_hash']:
            raise HTTPException(409, 'This sharing request belongs to another article or connection')
        if previous['state'] == 'confirmed':
            response = _pin_result(previous['pin_id'], connection['username'])
            return remember_article_share(article, 'pinterest', response, actor=actor, request=request,
                                          article_url=target, post_url=response['post_url'], event_id=previous['pin_id'])
        return {'status': 'unconfirmed', 'shared': False, 'username': connection['username']}
    record = {'article_id': article.id, 'session_hash': connection['session_hash'],
              'state': 'pending', 'created_at': utc_now()}
    writes = []
    add_dynamodb_put_transact(writes, key, record, new_pk_only=True)
    # Guard the actual article and connection immediately before sending a remote write.
    from app_config import get_dynamodb_table_name
    writes.append({'ConditionCheck': {'TableName': get_dynamodb_table_name(),
        'Key': {'pk': f'POST#{article.id}', 'sk': 'META'},
        'ConditionExpression': '#s = :published AND user_id = :owner',
        'ExpressionAttributeNames': {'#s': 'status'},
        'ExpressionAttributeValues': {':published': 'published', ':owner': actor.id}}})
    guard = {'Key': {'pk': f'USER#{actor.id}', 'sk': INTEGRATION_KEY},
             'TableName': get_dynamodb_table_name()}
    _guard(guard, connection)
    guard['ConditionExpression'] += ' AND board_id = :board AND encrypted_token = :token'
    guard['ExpressionAttributeValues'].update({':board': connection['board_id'], ':token': connection['encrypted_token']})
    writes.append({'ConditionCheck': guard})
    add_account_guard(writes, actor.id)
    active_guard = writes[-1]['ConditionCheck']
    active_guard['ConditionExpression'] += ' AND (attribute_not_exists(#active_status) OR #active_status = :active)'
    active_guard['ExpressionAttributeNames'] = {'#active_status': 'status'}
    active_guard['ExpressionAttributeValues'] = {':active': 'active'}
    _write(writes)
    try:
        result = pinterest_call('/pins', method='POST', token=token, payload={
            'board_id': connection['board_id'], 'title': article.title[:100],
            'description': html_to_text(article.preview or '')[:800], 'link': target,
            'media_source': {'source_type': 'image_url', 'url': media['url']},
        })
        pin_id = str(result.get('id', ''))
        if not pin_id.isdigit():
            raise PinterestRequestUnconfirmed(502, 'Pinterest did not confirm the Pin')
    except PinterestRequestUnconfirmed:
        return {'status': 'unconfirmed', 'shared': False, 'username': connection['username']}
    except HTTPException:
        # Confirmed rejections are safe for a new explicit attempt; do not retry this event.
        raise
    writes = []
    add_dynamodb_update_transact(writes, key, {'state': 'confirmed', 'pin_id': pin_id}, add_updated_at=False)
    add_account_guard(writes, actor.id)
    try:
        _write(writes)
    except (HTTPException, DynamoDBTransactionError, ClientError):
        logger.warning('Could not store confirmed Pinterest share receipt')
    response = _pin_result(pin_id, connection['username'])
    return remember_article_share(article, 'pinterest', response, actor=actor, request=request,
                                  article_url=target, post_url=response['post_url'], event_id=pin_id)
