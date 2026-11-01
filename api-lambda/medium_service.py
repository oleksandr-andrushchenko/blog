"""API-owned Medium connections using existing integration tokens."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, InvalidToken

from app_config import get_config
from article_sharing import article_media, authorize_article_owner, remember_article_share
from query_dtos import ArticleStatus, UserStatus
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, find_category,
    get_article_url, get_dynamodb_item, html_to_text, jinja2_env,
)
from web import HTTPException

INTEGRATION_KEY = 'INTEGRATION#medium'


class MediumRequestUnconfirmed(HTTPException):
    """A remote write may have succeeded; never automatically repeat it."""


def authorize_connection(user, actor):
    if user.id != actor.id:
        raise HTTPException(403, 'Only the account owner can manage integrations')
    if actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, 'An active account is required')


def _cipher():
    try:
        return Fernet((get_config().get('medium_token_encryption_key') or '').encode())
    except (ValueError, TypeError):
        raise HTTPException(503, 'Medium connections are not configured yet') from None


def medium_call(path, token, *, payload=None):
    """Keep credentials out of URLs and provider errors; do not retry writes."""
    request = Request('https://api.medium.com/v1' + path,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                               'Content-Type': 'application/json'},
                      method='POST' if payload is not None else 'GET')
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as error:
        if error.code in (401, 403):
            raise HTTPException(409, 'Reconnect Medium with a valid token that allows publishing') from None
        if error.code == 429:
            raise HTTPException(429, 'Medium rate limit reached. Wait before trying again') from None
        if error.code in (400, 404, 413, 422):
            raise HTTPException(422, 'Medium rejected the request. Check your connection and article') from None
        raise MediumRequestUnconfirmed(502, 'Medium did not confirm the request. Check your Medium profile before retrying') from None
    except (URLError, OSError, ValueError):
        raise MediumRequestUnconfirmed(502, 'Medium did not confirm the request. Check your Medium profile before retrying') from None
    if not isinstance(result, dict) or not isinstance(result.get('data'), dict):
        raise MediumRequestUnconfirmed(502, 'Medium did not confirm the request. Check your Medium profile before retrying')
    return result['data']


def _read(user_id):
    return get_dynamodb_item(f'USER#{user_id}', INTEGRATION_KEY, consistent_read=True)


def _write(user_id, connection, previous):
    writes = []
    key = (f'USER#{user_id}', INTEGRATION_KEY)
    if connection is None:
        add_dynamodb_delete_transact(writes, key)
        operation = writes[-1]['Delete']
    else:
        add_dynamodb_put_transact(writes, key, connection)
        operation = writes[-1]['Put']
    operation['ConditionExpression'] = 'encrypted_token = :previous' if previous else 'attribute_not_exists(pk)'
    if previous:
        operation['ExpressionAttributeValues'] = {':previous': previous['encrypted_token']}
    add_dynamodb_update_transact(writes, (f'USER#{user_id}', 'META'),
                                 changes={'has_medium_integration': connection is not None}, add_updated_at=False)
    writes[-1]['Update']['ConditionExpression'] = 'attribute_exists(pk)'
    try:
        dynamodb_transact_write(writes)
    except (DynamoDBTransactionError, ClientError) as error:
        response = getattr(error, 'response', None) or getattr(error.__context__, 'response', {})
        reasons = {reason.get('Code') for reason in response.get('CancellationReasons', [])
                   if reason and reason.get('Code') not in (None, 'None')}
        if (getattr(error, 'is_conditional', lambda: False)() or
                bool(reasons) and reasons <= {'ConditionalCheckFailed', 'TransactionConflict'}):
            raise HTTPException(409, 'Your Medium connection changed. Reload your profile and try again') from None
        raise


def _credentials(user_id, connection):
    try:
        identity = json.loads(_cipher().decrypt(connection['encrypted_token'].encode()))
        if identity['user_id'] != user_id or identity['medium_user_id'] != connection['medium_user_id']:
            raise ValueError('Identity mismatch')
        return identity['token']
    except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(409, 'Reconnect Medium in your profile’s Integrations tab') from None


def connection_status(user, actor):
    authorize_connection(user, actor)
    connection = _read(user.id)
    try:
        _cipher()
    except HTTPException:
        return {'configured': False, 'connected': False, 'reconnect_required': bool(connection)}
    if connection:
        try:
            _credentials(user.id, connection)
        except HTTPException:
            return {'configured': True, 'connected': False, 'reconnect_required': True}
    return {'configured': True, 'connected': bool(connection),
            **({'username': connection['username']} if connection else {})}


def connect(user, actor, payload):
    authorize_connection(user, actor)
    cipher = _cipher()
    token = payload.get('token') if isinstance(payload, dict) else None
    if (not isinstance(token, str) or not 1 <= len(token.strip()) <= 4096 or
            any(ord(c) < 33 or ord(c) > 126 for c in token.strip())):
        raise HTTPException(422, 'Enter a valid Medium integration token')
    token = token.strip()
    previous = _read(user.id)
    profile = medium_call('/me', token)
    if (not isinstance(profile.get('id'), str) or not profile['id'] or
            not isinstance(profile.get('username'), str) or not profile['username']):
        raise HTTPException(502, 'Medium did not confirm your account')
    identity = {'user_id': user.id, 'medium_user_id': profile['id'], 'token': token}
    connection = {'medium_user_id': profile['id'], 'username': profile['username'],
                  'encrypted_token': cipher.encrypt(json.dumps(identity).encode()).decode()}
    _write(user.id, connection, previous)
    return {'connected': True, 'username': profile['username']}


def disconnect(user, actor):
    authorize_connection(user, actor)
    _write(user.id, None, _read(user.id))
    return {'connected': False}


def share_article(article, actor, request):
    authorize_connection(actor, actor)
    authorize_article_owner(article, actor)
    if article.status != ArticleStatus.PUBLISHED:
        raise HTTPException(404, 'Only published articles can be shared to Medium')
    connection = _read(actor.id)
    if not connection:
        raise HTTPException(409, 'Connect Medium in your profile’s Integrations tab first')
    token = _credentials(actor.id, connection)
    url = get_article_url(request, article, absolute=True)
    category = find_category(article.category) if article.category else None
    # Only the public preview is exported, including for premium articles.
    description = html_to_text(article.preview) or ''
    media = article_media(article, request)
    content = jinja2_env().get_template('medium/article-post.html').render(
        title=article.title, description=description,
        category_name=category.name if category else article.category,
        article_url=url, cover_url=media[0]['url'] if media else None)
    current = _read(actor.id)
    if not current or current['encrypted_token'] != connection['encrypted_token']:
        raise HTTPException(409, 'Your Medium connection changed. Reload the page and try again')
    try:
        post = medium_call('/users/' + quote(connection['medium_user_id'], safe='') + '/posts', token,
                           payload={'title': article.title[:100], 'contentFormat': 'html', 'content': content,
                                    'canonicalUrl': url, 'publishStatus': 'public'})
    except MediumRequestUnconfirmed:
        return {'shared': False, 'status': 'unconfirmed'}
    if (not isinstance(post.get('id'), str) or not post['id'] or
            post.get('publishStatus') != 'public' or post.get('authorId') != connection['medium_user_id']):
        return {'shared': False, 'status': 'unconfirmed'}
    post_url = post.get('url')
    parts = urlsplit(post_url) if isinstance(post_url, str) else None
    if not parts or parts.scheme != 'https' or parts.hostname != 'medium.com' or parts.username or parts.password:
        post_url = None
    return remember_article_share(article, 'medium', {'shared': True, 'post_id': post['id'], 'post_url': post_url},
                                  actor=actor, request=request, article_url=url, post_url=post_url)
