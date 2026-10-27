"""Connect another proven login identity to the authenticated application account."""
import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlencode

from app_config import config, get_dynamodb_table_name, is_prod
from cognito_auth import exchange_login_code
from query_dtos import UserStatus
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, get_dynamodb_item,
    get_url, utc_now,
)
from web import HTTPException

PENDING_KEY = 'LOGIN_LINK'
COOKIE_NAME = 'login_connection'
CONNECT_LIFETIME_MS = 10 * 60 * 1000


class LoginLinkError(HTTPException):
    def __init__(self, reason, detail='Connection expired. Start again from your profile'):
        super().__init__(409, detail)
        self.reason = reason


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _authorize(user, actor):
    if user.id != actor.id or actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, 'Only the active account owner can connect login methods')


def _guard(operation, pending):
    operation['ConditionExpression'] = 'state_hash = :state AND binding_hash = :binding AND auth_hash = :auth'
    operation['ExpressionAttributeValues'] = {':state': pending['state_hash'],
                                              ':binding': pending['binding_hash'], ':auth': pending['auth_hash']}


def begin_connection(user, actor, request):
    _authorize(user, actor)
    auth_cookie = request.cookies.get('token')
    if not auth_cookie:
        raise HTTPException(401, 'Sign in before connecting another login method')
    if is_prod() and not all(config.get(key) for key in (
            'cognito_domain', 'cognito_client_id', 'cognito_client_secret', 'cognito_user_pool_id', 'aws_region')):
        raise HTTPException(503, 'Login connections are not configured yet')
    state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    now = utc_now()
    pending = {'state_hash': _digest(state), 'binding_hash': _digest(binding), 'auth_hash': _digest(auth_cookie),
               'verifier': verifier, 'expires_at': now + CONNECT_LIFETIME_MS, 'created_at': now}
    writes = []
    add_dynamodb_put_transact(writes, (f'USER#{user.id}', PENDING_KEY), pending)
    dynamodb_transact_write(writes)
    callback = get_url(request, 'login-link-callback', absolute=True)
    params = {'response_type': 'code', 'client_id': config.get('cognito_client_id') or 'local',
              'redirect_uri': callback, 'scope': 'openid email profile', 'state': state, 'nonce': state,
              'code_challenge_method': 'S256', 'prompt': 'login',
              'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
    # /login displays the configured choices even when a Cognito SSO cookie exists.
    destination = f'https://{config["cognito_domain"]}/login' if is_prod() else callback
    return {'connect_url': destination + '?' + urlencode(params)}, binding


def complete_connection(actor, request):
    _authorize(actor, actor)
    state = request.query_params.get('state') or ''
    binding, auth_cookie = request.cookies.get(COOKIE_NAME) or '', request.cookies.get('token') or ''
    if not state or not binding or not auth_cookie or len(state) > 128 or len(binding) > 128:
        raise LoginLinkError('expired')
    key = (f'USER#{actor.id}', PENDING_KEY)
    pending = get_dynamodb_item(*key, consistent_read=True)
    now = utc_now()
    if (not pending or pending['expires_at'] <= now or pending.get('claimed')
            or not hmac.compare_digest(pending['state_hash'], _digest(state))
            or not hmac.compare_digest(pending['binding_hash'], _digest(binding))
            or not hmac.compare_digest(pending['auth_hash'], _digest(auth_cookie))):
        raise LoginLinkError('expired')
    # Consume the callback before exchanging a single-use authorization code.
    claim = []
    add_dynamodb_update_transact(claim, key, changes={'claimed': True}, add_updated_at=False)
    operation = claim[0]['Update']
    _guard(operation, pending)
    operation['ConditionExpression'] += ' AND expires_at > :now AND attribute_not_exists(claimed)'
    operation['ExpressionAttributeValues'][':now'] = now
    # Preserve the update helper's value for SET claimed = true.
    operation['ExpressionAttributeValues'][':new_claimed'] = True
    try:
        dynamodb_transact_write(claim)
    except DynamoDBTransactionError as error:
        if error.is_conditional():
            raise LoginLinkError('expired') from None
        raise
    writes = []
    add_dynamodb_delete_transact(writes, key)
    _guard(writes[0]['Delete'], pending)
    if request.query_params.get('error'):
        dynamodb_transact_write(writes)
        return 'denied'
    code = request.query_params.get('code')
    if not code or len(code) > 4096:
        raise LoginLinkError('failed', 'Sign-in did not finish. Please connect again from your profile')
    identity = exchange_login_code(code, get_url(request, 'login-link-callback', absolute=True),
                                   verifier=pending['verifier'], nonce=state)
    if not identity.sub or not identity.iss:
        raise LoginLinkError('failed')
    provider_key = (f'PROVIDER_USER#{identity.iss.split("/")[-1]}#{identity.sub}', 'META')
    existing = get_dynamodb_item(*provider_key, consistent_read=True)
    if existing and existing['user_id'] != actor.id:
        raise LoginLinkError('in_use', 'This login belongs to another account. Sign in with it to access that account')
    if not existing:
        add_dynamodb_put_transact(writes, provider_key, {'user_id': actor.id}, new_pk_only=True)
    else:
        writes.append({'ConditionCheck': {
            'TableName': get_dynamodb_table_name(), 'Key': {'pk': provider_key[0], 'sk': provider_key[1]},
            'ConditionExpression': 'user_id = :owner', 'ExpressionAttributeValues': {':owner': actor.id},
        }})
    # A new attempt or an account ban cancels the whole association transaction.
    writes.append({'ConditionCheck': {
        'TableName': get_dynamodb_table_name(), 'Key': {'pk': f'USER#{actor.id}', 'sk': 'META'},
        'ConditionExpression': '#status = :active',
        'ExpressionAttributeNames': {'#status': 'status'}, 'ExpressionAttributeValues': {':active': UserStatus.ACTIVE},
    }})
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if error.is_conditional():
            current = get_dynamodb_item(*provider_key, consistent_read=True)
            if current and current['user_id'] != actor.id:
                raise LoginLinkError('in_use') from None
            raise LoginLinkError('expired') from None
        raise
    return 'already_connected' if existing else 'connected'
