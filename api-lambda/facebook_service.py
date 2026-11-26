"""Administrator-managed site Facebook Page connection and explicit article publishing."""
import hashlib
import hmac
import json
import re
import secrets
import uuid
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request as URLRequest, build_opener

from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, InvalidToken

from account_write_guards import add_account_guard
from api_route_metadata import API_URL_ROUTES
from app_config import config, get_dynamodb_table_name, get_static_files_dir, get_static_s3_bucket
from article_sharing import article_media, authorize_article_owner, remember_article_share
from query_dtos import ArticleStatus, UserStatus
from shared_utils import (
    DynamoDBTransactionError, Permission, add_dynamodb_delete_transact,
    add_dynamodb_put_transact, add_dynamodb_update_transact, dynamodb_transact_write,
    get_dynamodb_item, get_article_url, html_to_text, logger, utc_now, verify_authorization,
)
from web import HTTPException

COOKIE_NAME = 'facebook_page_connection'
CONNECT_LIFETIME_MS = 10 * 60 * 1000
PENDING_KEY = 'INTEGRATION#facebook#pending'
SELECTION_KEY = 'INTEGRATION#facebook#selection'
INTEGRATION_KEY = 'INTEGRATION#facebook'
SITE_KEY = ('SITE#facebook', 'META')
SCOPES = {'pages_show_list', 'pages_read_engagement', 'pages_manage_posts'}
MAX_PHOTO_BYTES = 10_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


open_graph_request = build_opener(_NoRedirect()).open


class FacebookUnconfirmed(HTTPException):
    """A remote write may have succeeded; never repeat it automatically."""


def _authorize(actor):
    verify_authorization(actor, Permission.MANAGE_FACEBOOK_PAGE)
    if actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, 'An active account is required')


def _cipher():
    try:
        return Fernet((config.get('facebook_page_token_encryption_key') or '').encode())
    except (ValueError, TypeError):
        raise HTTPException(503, 'Facebook Page publishing is not configured') from None


def _settings():
    if not all(config.get(key) for key in ('facebook_page_app_id', 'facebook_page_app_secret')):
        raise HTTPException(503, 'Facebook Page publishing is not configured')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+', config.get('facebook_page_api_version', '')):
        raise HTTPException(503, 'Facebook Page API version is not configured')
    for key in ('api_base_url', 'web_base_url'):
        url = urlsplit(config.get(key) or '')
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise HTTPException(503, 'Facebook Page connections require HTTPS web and API URLs')
    _cipher()
    return config


def callback_url():
    return _settings()['api_base_url'].rstrip('/') + API_URL_ROUTES['facebook-page-callback']


def facebook_call(path, *, method='GET', token=None, form=None, photo=None, **params):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
        params['appsecret_proof'] = hmac.new(
            config['facebook_page_app_secret'].encode(), token.encode(), hashlib.sha256
        ).hexdigest()
    body = None
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        body = urlencode(form).encode()
    url = 'https://graph.facebook.com/' + config['facebook_page_api_version'] + path
    if params:
        url += '?' + urlencode(params)
    if photo is not None:
        import httpx

        multipart = httpx.Request('POST', url, data=form, files={'source': ('photo.jpg', photo, 'image/jpeg')})
        headers['Content-Type'] = multipart.headers['Content-Type']
        body = multipart.read()
    try:
        timeout = 15 if method == 'POST' and path.endswith('/feed') else 8
        with open_graph_request(URLRequest(url, data=body, headers=headers, method=method), timeout=timeout) as response:
            if method == 'POST' and response.status not in (200, 201):
                raise FacebookUnconfirmed(502, 'Facebook did not confirm the request. Check the Page before retrying')
            result = json.load(response)
    except HTTPError as error:
        code = None
        try:
            details = json.load(error).get('error', {})
            code = details.get('code')
        except (ValueError, AttributeError, TypeError, OSError):
            pass
        logger.warning('Facebook request failed: status=%s code=%s', error.code,
                       code if type(code) is int else None)
        if error.code >= 500 or 300 <= error.code < 400:
            raise FacebookUnconfirmed(502, 'Facebook did not confirm the request. Check the Page before retrying') from None
        if code == 190 or error.code in (401, 403):
            raise HTTPException(409, 'Reconnect Facebook in Utils and allow Page publishing access') from None
        if error.code == 429 or code in (4, 17, 32, 613):
            raise HTTPException(429, 'Facebook rate limit reached. Wait before trying again') from None
        raise HTTPException(422, 'Facebook rejected the request. Check Page access and permissions') from None
    except (URLError, TimeoutError, ValueError, OSError):
        raise FacebookUnconfirmed(502, 'Facebook did not confirm the request. Check the Page before retrying') from None
    if not isinstance(result, dict) or 'error' in result:
        raise FacebookUnconfirmed(502, 'Facebook returned an incomplete response. Check the Page before retrying')
    return result


def _read(actor_id, key):
    return get_dynamodb_item('USER#' + actor_id, key, consistent_read=True)


def _write(writes):
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if error.is_conditional():
            raise HTTPException(409, 'The Facebook connection changed or expired. Reload and try again') from None
        raise


def _guard(operation, record):
    operation['ConditionExpression'] = 'session_hash = :session' if record else 'attribute_not_exists(pk)'
    if record:
        operation['ExpressionAttributeValues'] = {':session': record['session_hash']}


def _active_guard(writes, owner_id):
    add_account_guard(writes, owner_id)
    operation = writes[-1]['ConditionCheck']
    operation['ConditionExpression'] += ' AND (attribute_not_exists(#s) OR #s = :active)'
    operation['ExpressionAttributeNames'] = {'#s': 'status'}
    operation['ExpressionAttributeValues'] = {':active': 'active'}


def _encrypt(actor_id, session, token, **identity):
    return _cipher().encrypt(json.dumps({'owner_id': actor_id, 'session_hash': session,
        'app_id': config['facebook_page_app_id'], 'access_token': token, **identity}).encode()).decode()


def _decrypt(actor_id, record):
    try:
        value = json.loads(_cipher().decrypt(record['encrypted_token'].encode()))
        if (value['owner_id'] != actor_id or value['session_hash'] != record['session_hash']
                or value['app_id'] != config['facebook_page_app_id'] or record['app_id'] != value['app_id']
                or value.get('page_id') != record.get('page_id') or not value['access_token']):
            raise ValueError('Identity mismatch')
        return value['access_token']
    except (InvalidToken, KeyError, ValueError, TypeError):
        raise HTTPException(409, 'Reconnect Facebook in Utils') from None


def _connection():
    pointer = get_dynamodb_item(*SITE_KEY, consistent_read=True)
    if not pointer:
        return None, None
    if not pointer.get('owner_id'):
        return pointer, None
    owner = _read(pointer['owner_id'], 'META')
    record = _read(pointer['owner_id'], INTEGRATION_KEY)
    if (not owner or owner.get('deletion_id') or owner.get('status', 'active') != 'active'
            or not record or record.get('session_hash') != pointer['session_hash']
            or record.get('app_id') != config['facebook_page_app_id']):
        return pointer, None
    return pointer, record


def connection_status(actor, auth_cookie):
    _authorize(actor)
    try:
        _settings()
    except HTTPException:
        return {'configured': False, 'connected': False, 'can_select': False}
    _, record = _connection()
    selection = _read(actor.id, SELECTION_KEY)
    return {'configured': True, 'connected': bool(record),
            'page_name': record['page_name'] if record else None,
            'page_id': record['page_id'] if record else None,
            'can_select': _can_select(selection, auth_cookie)}


def begin_connection(actor, auth_cookie):
    _authorize(actor)
    _settings()
    if not auth_cookie:
        raise HTTPException(401)
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = utc_now() + CONNECT_LIFETIME_MS
    pointer, _ = _connection()
    record = {'site_session': pointer['session_hash'] if pointer else '',
              'session_hash': hashlib.sha256(state.encode()).hexdigest(),
              'binding_hash': hashlib.sha256(binding.encode()).hexdigest(),
              'auth_hash': hashlib.sha256(auth_cookie.encode()).hexdigest(),
              'expires_at': expires, 'ttl': expires // 1000}
    writes = []
    add_dynamodb_put_transact(writes, ('USER#' + actor.id, PENDING_KEY), record)
    add_dynamodb_delete_transact(writes, ('USER#' + actor.id, SELECTION_KEY))
    _active_guard(writes, actor.id)
    _write(writes)
    url = 'https://www.facebook.com/' + config['facebook_page_api_version'] + '/dialog/oauth?' + urlencode({
        'client_id': config['facebook_page_app_id'], 'redirect_uri': callback_url(),
        'scope': ','.join(sorted(SCOPES)), 'response_type': 'code', 'state': state, 'auth_type': 'rerequest',
    })
    return {'connect_url': url}, binding


def complete_connection(actor, *, state, binding, code, auth_cookie, denied=False):
    _authorize(actor)
    _settings()
    if not state or not binding or not auth_cookie or len(state) > 128 or len(binding) > 128:
        raise HTTPException(409, 'Facebook connection expired. Connect again in Utils')
    pending = _read(actor.id, PENDING_KEY)
    digest = hashlib.sha256(state.encode()).hexdigest()
    if (not pending or pending['expires_at'] <= utc_now() or pending.get('claimed')
            or not hmac.compare_digest(pending['session_hash'], digest)
            or not hmac.compare_digest(pending['binding_hash'], hashlib.sha256(binding.encode()).hexdigest())
            or not hmac.compare_digest(pending['auth_hash'], hashlib.sha256(auth_cookie.encode()).hexdigest())):
        raise HTTPException(409, 'Facebook connection expired. Connect again in Utils')
    writes = []
    add_dynamodb_put_transact(writes, ('USER#' + actor.id, PENDING_KEY), {**pending, 'claimed': True})
    operation = writes[-1]['Put']
    _guard(operation, pending)
    operation['ConditionExpression'] += ' AND expires_at > :now AND attribute_not_exists(claimed)'
    operation['ExpressionAttributeValues'][':now'] = utc_now()
    _active_guard(writes, actor.id)
    _write(writes)
    if denied:
        return False
    if not code or len(code) > 4096:
        raise HTTPException(422, 'Facebook did not authorize the connection')
    credentials = {'client_id': config['facebook_page_app_id'], 'client_secret': config['facebook_page_app_secret']}
    tokens = facebook_call('/oauth/access_token', **credentials, code=code, redirect_uri=callback_url())
    access = tokens.get('access_token')
    if not isinstance(access, str) or not access:
        raise HTTPException(502, 'Facebook did not confirm access')
    tokens = facebook_call('/oauth/access_token', **credentials,
                           grant_type='fb_exchange_token', fb_exchange_token=access)
    access = tokens.get('access_token')
    if not isinstance(access, str) or not access:
        raise HTTPException(502, 'Facebook did not confirm access')
    permissions = facebook_call('/me/permissions', token=access).get('data')
    granted = {item['permission'] for item in permissions
               if isinstance(item, dict) and isinstance(item.get('permission'), str)
               and item.get('status') == 'granted'} if isinstance(permissions, list) else set()
    if not SCOPES <= granted:
        raise HTTPException(409, 'Reconnect Facebook and allow all requested Page permissions')
    expires = utc_now() + CONNECT_LIFETIME_MS
    record = {'site_session': pending['site_session'], 'session_hash': digest, 'app_id': config['facebook_page_app_id'],
              'encrypted_token': _encrypt(actor.id, digest, access), 'expires_at': expires,
              'auth_hash': pending['auth_hash'], 'ttl': expires // 1000}
    writes = []
    add_dynamodb_delete_transact(writes, ('USER#' + actor.id, PENDING_KEY))
    operation = writes[-1]['Delete']
    _guard(operation, pending)
    operation['ConditionExpression'] += ' AND claimed = :yes AND expires_at > :now'
    operation['ExpressionAttributeValues'].update({':yes': True, ':now': utc_now()})
    add_dynamodb_put_transact(writes, ('USER#' + actor.id, SELECTION_KEY), record)
    _active_guard(writes, actor.id)
    _write(writes)
    return True


def _can_select(selection, auth_cookie):
    return bool(selection and auth_cookie and selection['expires_at'] > utc_now()
                and hmac.compare_digest(selection['auth_hash'], hashlib.sha256(auth_cookie.encode()).hexdigest()))


def _pages(actor, auth_cookie):
    _authorize(actor)
    _settings()
    selection = _read(actor.id, SELECTION_KEY)
    if not _can_select(selection, auth_cookie):
        raise HTTPException(409, 'Connect Facebook again to choose a Page')
    token = _decrypt(actor.id, selection)
    pages, after, seen = [], None, set()
    for _ in range(100):
        params = {'fields': 'id,name,access_token,tasks', 'limit': 100}
        if after:
            params['after'] = after
        result = facebook_call('/me/accounts', token=token, **params)
        if not isinstance(result.get('data'), list):
            raise HTTPException(502, 'Could not load Facebook Pages')
        for page in result['data']:
            if (isinstance(page, dict) and re.fullmatch(r'[0-9]{1,30}', str(page.get('id', '')))
                    and isinstance(page.get('tasks'), list) and 'CREATE_CONTENT' in page['tasks']
                    and isinstance(page.get('access_token'), str)
                    and page['access_token']):
                pages.append(page)
        paging = result.get('paging') or {}
        if not isinstance(paging, dict):
            break
        if not paging.get('next'):
            return selection, pages
        cursors = paging.get('cursors')
        after = cursors.get('after') if isinstance(cursors, dict) else None
        if not isinstance(after, str) or not after or after in seen or len(after) > 2048:
            break
        seen.add(after)
    raise HTTPException(502, 'Could not finish loading Facebook Pages')


def list_pages(actor, auth_cookie):
    _, pages = _pages(actor, auth_cookie)
    return {'pages': [{'id': str(page['id']), 'name': str(page.get('name') or 'Page')[:180]} for page in pages]}


def select_page(actor, page_id, auth_cookie):
    _authorize(actor)
    if not isinstance(page_id, str) or not re.fullmatch(r'[0-9]{1,30}', page_id):
        raise HTTPException(422, 'Choose a Facebook Page')
    selection, pages = _pages(actor, auth_cookie)
    page = next((page for page in pages if str(page['id']) == page_id), None)
    if not page:
        raise HTTPException(422, 'Choose a Page where you have publishing access')
    pointer, _ = _connection()
    if (pointer['session_hash'] if pointer else '') != selection['site_session']:
        raise HTTPException(409, 'The site Page changed. Connect Facebook again')
    session = secrets.token_hex(32)
    record = {'session_hash': session, 'app_id': config['facebook_page_app_id'],
              'page_id': page_id, 'page_name': str(page.get('name') or 'Page')[:180],
              'encrypted_token': _encrypt(actor.id, session, page['access_token'], page_id=page_id)}
    writes = []
    add_dynamodb_delete_transact(writes, ('USER#' + actor.id, SELECTION_KEY))
    operation = writes[-1]['Delete']
    _guard(operation, selection)
    operation['ConditionExpression'] += ' AND expires_at > :now'
    operation['ExpressionAttributeValues'][':now'] = utc_now()
    add_dynamodb_put_transact(writes, ('USER#' + actor.id, INTEGRATION_KEY), record)
    if pointer and pointer.get('owner_id') and pointer['owner_id'] != actor.id:
        previous = _read(pointer['owner_id'], INTEGRATION_KEY)
        if previous and previous['session_hash'] == pointer['session_hash']:
            add_dynamodb_delete_transact(writes, ('USER#' + pointer['owner_id'], INTEGRATION_KEY))
            _guard(writes[-1]['Delete'], previous)
    add_dynamodb_put_transact(writes, SITE_KEY, {'owner_id': actor.id, 'session_hash': session})
    _guard(writes[-1]['Put'], pointer)
    _active_guard(writes, actor.id)
    _write(writes)
    return connection_status(actor, auth_cookie)


def disconnect(actor):
    _authorize(actor)
    pointer, _ = _connection()
    writes = []
    add_dynamodb_put_transact(writes, SITE_KEY, {'session_hash': secrets.token_hex(32)})
    _guard(writes[-1]['Put'], pointer)
    if pointer and pointer.get('owner_id'):
        record = _read(pointer['owner_id'], INTEGRATION_KEY)
        if record and record['session_hash'] == pointer['session_hash']:
            add_dynamodb_delete_transact(writes, ('USER#' + pointer['owner_id'], INTEGRATION_KEY))
            _guard(writes[-1]['Delete'], record)
    for key in (PENDING_KEY, SELECTION_KEY):
        add_dynamodb_delete_transact(writes, ('USER#' + actor.id, key))
    _active_guard(writes, actor.id)
    _write(writes)
    return {'connected': False}


def _cover_for_upload(media):
    """Convert app-hosted non-JPEG/PNG covers with the existing image stack."""
    filename = media['filename']
    cover_url = urlsplit(media['url'])
    if cover_url.scheme != 'https' or not cover_url.hostname or cover_url.username or cover_url.password:
        raise HTTPException(422, 'Facebook needs a public HTTPS URL for the article cover')
    if urlsplit(filename).scheme or PurePosixPath(filename).suffix.lower() in ('.jpg', '.jpeg', '.png'):
        return {'published': 'false', 'url': media['url']}, None

    from PIL import Image, ImageOps

    # The article stores an app-generated static key. Keep reads inside the public storage boundary.
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9]+', filename):
        raise HTTPException(422, 'Invalid article cover filename')
    try:
        bucket = get_static_s3_bucket()
        if bucket:
            from api_utils import get_s3_client

            body = get_s3_client().get_object(Bucket=bucket, Key=filename)['Body']
            try:
                source_bytes = body.read(MAX_PHOTO_BYTES + 1)
            finally:
                body.close()
        else:
            with (Path(get_static_files_dir()) / filename).open('rb') as source_file:
                source_bytes = source_file.read(MAX_PHOTO_BYTES + 1)
        if len(source_bytes) > MAX_PHOTO_BYTES:
            raise ValueError('cover is too large')
        with Image.open(BytesIO(source_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((2048, 2048))
            photo = BytesIO()
            image.convert('RGB').save(photo, 'JPEG', quality=90)
        return {'published': 'false'}, photo.getvalue()
    except (OSError, ValueError, ClientError, Image.DecompressionBombError):
        raise HTTPException(422, 'Could not prepare the article cover for Facebook') from None


def _caption(article, link=None):
    hashtags = {}
    for tag in (article.category, *article.tags):
        name = ''.join(re.findall(r'[^\W_]+', tag.title()))
        if name and not name.isdecimal():
            hashtags.setdefault(name.casefold(), '#' + name)
    preview = html_to_text(article.preview or '')[:4000].strip()
    return '\n\n'.join(part for part in (article.title, preview, link, ' '.join(hashtags.values())) if part)


def _confirmed_share(article, actor, request, target, connection, post_id):
    response = {'shared': True, 'post_id': post_id, 'page_name': connection['page_name'],
                'post_url': 'https://www.facebook.com/' + post_id}
    return remember_article_share(article, 'facebook', response, actor=actor, request=request,
                                  article_url=target, post_url=response['post_url'], event_id=post_id)


def share_article(article, actor, request, event_id):
    _authorize(actor)
    authorize_article_owner(article, actor)
    if article.status != ArticleStatus.PUBLISHED:
        raise HTTPException(404, 'Publish this article before sharing it')
    _settings()
    try:
        event_id = str(uuid.UUID(event_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, 'Invalid Facebook sharing request') from None
    pointer, connection = _connection()
    if not connection:
        raise HTTPException(409, 'Connect a Facebook Page in Utils first')
    target = get_article_url(request, article, absolute=True)
    key = ('USER#' + actor.id, 'FACEBOOK_PUBLISH#' + event_id)
    previous = get_dynamodb_item(*key, consistent_read=True)
    if previous:
        if previous.get('article_id') != article.id or previous.get('session_hash') != connection['session_hash']:
            raise HTTPException(409, 'This request belongs to another article or Page connection')
        if previous.get('post_id'):
            return _confirmed_share(article, actor, request, target, connection, previous['post_id'])
        return {'shared': False, 'status': 'unconfirmed'}

    token = _decrypt(pointer['owner_id'], connection)
    media = article_media(article, request)
    cover = _cover_for_upload(media[0]) if media else None
    page_path = '/' + connection['page_id']
    page = facebook_call(page_path, token=token, fields='id')
    if str(page.get('id')) != connection['page_id']:
        raise HTTPException(409, 'Reconnect Facebook in Utils')

    writes = []
    add_dynamodb_put_transact(writes, key, {'article_id': article.id, 'session_hash': connection['session_hash'],
                                          'created_at': utc_now()}, new_pk_only=True)
    table = get_dynamodb_table_name()
    for record_key in (SITE_KEY, ('USER#' + pointer['owner_id'], INTEGRATION_KEY)):
        writes.append({'ConditionCheck': {
            'TableName': table, 'Key': {'pk': record_key[0], 'sk': record_key[1]},
            'ConditionExpression': 'session_hash = :session',
            'ExpressionAttributeValues': {':session': connection['session_hash']},
        }})
    writes.append({'ConditionCheck': {
        'TableName': table, 'Key': {'pk': 'POST#' + article.id, 'sk': 'META'},
        'ConditionExpression': '#status = :published AND user_id = :owner',
        'ExpressionAttributeNames': {'#status': 'status'},
        'ExpressionAttributeValues': {':published': ArticleStatus.PUBLISHED.value, ':owner': actor.id},
    }})
    for owner_id in {actor.id, pointer['owner_id']}:
        _active_guard(writes, owner_id)
    try:
        _write(writes)
    except HTTPException as error:
        if error.status_code == 409 and get_dynamodb_item(*key, consistent_read=True):
            return {'shared': False, 'status': 'unconfirmed'}
        raise

    try:
        if cover:
            fields, photo = cover
            photo_result = facebook_call(page_path + '/photos', method='POST', token=token, form=fields, photo=photo)
            photo_id = photo_result.get('id')
            if not isinstance(photo_id, str) or not re.fullmatch(r'[0-9]+', photo_id):
                raise FacebookUnconfirmed(502, 'Facebook did not confirm the article cover')
            # A disconnected Page or unpublished article must not produce a public post.
            _write(writes[1:])
            form = {'message': _caption(article, target),
                    'attached_media[0]': json.dumps({'media_fbid': photo_id})}
        else:
            form = {'message': _caption(article), 'link': target}
        result = facebook_call(page_path + '/feed', method='POST', token=token, form=form)
        post_id = result.get('id')
        if not isinstance(post_id, str) or not re.fullmatch(re.escape(connection['page_id']) + r'_[0-9]+', post_id):
            raise FacebookUnconfirmed(502, 'Facebook did not confirm the post')
    except FacebookUnconfirmed:
        return {'shared': False, 'status': 'unconfirmed'}

    receipt_writes = []
    add_dynamodb_update_transact(receipt_writes, key, {'post_id': post_id}, add_updated_at=False)
    receipt = receipt_writes[-1]['Update']
    receipt['ConditionExpression'] = 'attribute_exists(pk) AND article_id = :article AND session_hash = :session'
    receipt['ExpressionAttributeValues'].update({':article': article.id, ':session': connection['session_hash']})
    _active_guard(receipt_writes, actor.id)
    try:
        _write(receipt_writes)
    except Exception:
        logger.warning('Could not save confirmed Facebook share receipt')
    return _confirmed_share(article, actor, request, target, connection, post_id)
