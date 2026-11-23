"""Browser-bound, single-use sign-in handoffs shared by the two auth boundaries."""
import base64
import hashlib
import hmac
import secrets
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

from app_config import config, get_auth_cookie_domain, get_dynamodb_table_name, get_web_base_url, is_prod
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, get_dynamodb_item, get_url, utc_now,
)
from web import HTTPException
from web_route_metadata import WEB_AUTH_URL_ROUTES

COOKIE = 'login_browser'
LIFETIME = 15 * 60 * 1000
CONTINUATION_KEY = 'LOGIN_CONTINUATION'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def return_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return '/'
    if '\\' in value or any(ord(char) < 32 for char in value):
        return '/'
    try:
        parsed, base = urlparse(value), urlparse(get_web_base_url())
        path = unquote(parsed.path).rstrip('/') or '/'
        if '\\' in path or any(ord(char) < 32 for char in path):
            return '/'
        if (parsed.netloc and (parsed.netloc != base.netloc or parsed.scheme not in {'', base.scheme})
                or not parsed.netloc and (parsed.scheme or not parsed.path.startswith('/'))
                or path.startswith('//') or path in WEB_AUTH_URL_ROUTES.values()):
            return '/'
    except ValueError:
        return '/'
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key == 'offset' for key, _ in query):
        value = parsed._replace(query=urlencode([(key, item) for key, item in query if key != 'offset'])).geturl()
    # Cookies and Location headers require ASCII; preserve existing URL escapes.
    return quote(value, safe="/#%[]=:;$&()+,!?*@'~")


def browser_cookie(response, value=None):
    options = dict(path='/', domain=get_auth_cookie_domain(), secure=is_prod(), httponly=True, samesite='lax')
    if value is None:
        response.delete_cookie(COOKIE, **options)
    else:
        response.set_cookie(COOKIE, value, max_age=LIFETIME // 1000, **options)


def begin_login(request, destination, *, intent=None):
    state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    now = utc_now()
    pending = {'binding': digest(binding), 'verifier': verifier, 'return_url': return_url(destination),
               'created_at': now, 'expires_at': now + LIFETIME, 'ttl': (now + LIFETIME) // 1000}
    if intent:
        pending['intent'] = intent
    writes = []
    add_dynamodb_put_transact(writes, ('LOGIN_FLOW#' + digest(state), 'META'), pending, new_pk_only=True)
    dynamodb_transact_write(writes)
    callback = get_url(request, 'login-callback', absolute=True)
    params = {'response_type': 'code', 'client_id': config.get('cognito_client_id') or 'local',
              'redirect_uri': callback, 'scope': 'openid email profile', 'state': state, 'nonce': state,
              'code_challenge_method': 'S256',
              'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
    provider = f'https://{config["cognito_domain"]}/oauth2/authorize' if is_prod() else callback
    return provider + '?' + urlencode(params), binding


def claim_login(request):
    state, binding = request.query_params.get('state', ''), request.cookies.get(COOKIE, '')
    if not is_prod() and not state and not binding:
        return None  # Local synthetic sign-in is also used by the isolated HTTP tests.
    if not state or not binding or len(state) > 128 or len(binding) > 128:
        raise HTTPException(400, 'Sign-in expired. Please start again')
    key = ('LOGIN_FLOW#' + digest(state), 'META')
    pending = get_dynamodb_item(*key, consistent_read=True)
    if (not pending or pending['expires_at'] <= utc_now()
            or not hmac.compare_digest(pending['binding'], digest(binding))):
        raise HTTPException(400, 'Sign-in expired. Please start again')
    writes = []
    add_dynamodb_delete_transact(writes, key)
    writes[-1]['Delete'].update(ConditionExpression='binding = :binding AND expires_at > :now',
                              ExpressionAttributeValues={':binding': pending['binding'], ':now': utc_now()})
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if error.is_conditional():
            raise HTTPException(400, 'Sign-in already completed or expired') from None
        raise
    return pending


def save_continuation(user, token, pending):
    writes = []
    now = utc_now()
    item = {'auth': digest(token), 'return_url': return_url(pending['return_url']),
            'expires_at': now + LIFETIME, 'ttl': (now + LIFETIME) // 1000}
    if pending.get('intent'):
        item['intent'] = pending['intent']
    add_dynamodb_put_transact(writes, (f'USER#{user.id}', CONTINUATION_KEY), item)
    writes.append({'ConditionCheck': {
        'TableName': get_dynamodb_table_name(), 'Key': {'pk': f'USER#{user.id}', 'sk': 'META'},
        'ConditionExpression': 'attribute_exists(pk) AND attribute_not_exists(deletion_id)',
    }})
    dynamodb_transact_write(writes)


def get_continuation(user, request):
    item = get_dynamodb_item(f'USER#{user.id}', CONTINUATION_KEY, consistent_read=True)
    if (not item or item['expires_at'] <= utc_now()
            or not hmac.compare_digest(item['auth'], digest(request.cookies.get('token', '')))):
        raise HTTPException(409, 'This sign-in action expired or has already completed')
    return item


def consume_continuation(user, item):
    writes = []
    add_dynamodb_update_transact(writes, (f'USER#{user.id}', CONTINUATION_KEY),
                                changes={'done': True, 'intent': None}, add_updated_at=False)
    operation = writes[-1]['Update']
    operation['ConditionExpression'] = '#auth = :auth AND expires_at > :now AND attribute_not_exists(#done)'
    operation['ExpressionAttributeNames'].update({'#done': 'done', '#auth': 'auth'})
    operation['ExpressionAttributeValues'].update({':auth': item['auth'], ':now': utc_now()})
    return writes
