"""Verify provider erasure callbacks before resolving application identities."""
import base64
from functools import lru_cache
import hashlib
import hmac
import json
import re
import time

import boto3

from app_config import config
from shared_utils import get_dynamodb_item, get_dynamodb_table
from web import HTTPException


@lru_cache(maxsize=1)
def _facebook_secret(arn):
    value = boto3.client('secretsmanager').get_secret_value(SecretId=arn)
    return json.loads(value['SecretString'])['app_secret']


def _decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def verified_meta_request(provider, signed_request):
    if provider == 'facebook':
        arn = config.get('facebook_credentials_secret_arn')
        secret = _facebook_secret(arn) if arn else None
    elif provider == 'threads':
        secret = config.get('threads_app_secret')
    else:
        raise HTTPException(404, 'Unknown provider')
    if not secret:
        raise HTTPException(503, 'Provider deletion is not configured')
    try:
        if not isinstance(signed_request, str) or len(signed_request) > 16384:
            raise ValueError()
        signature, encoded = signed_request.split('.')
        expected = hmac.digest(secret.encode(), encoded.encode(), 'sha256')
        if not hmac.compare_digest(_decode(signature), expected):
            raise ValueError()
        payload = json.loads(_decode(encoded))
        subject = payload['user_id']
        issued = payload['issued_at']
        if (payload.get('algorithm', '').upper() != 'HMAC-SHA256'
                or not isinstance(subject, str) or not re.fullmatch(r'[0-9]{1,128}', subject)
                or type(issued) is not int or issued <= 0 or issued > time.time() + 300):
            raise ValueError()
        # No short age limit: Meta can retry a genuine deletion days later.
        # Identity creation time is checked before resolving it to an account.
        return subject, issued * 1000
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise HTTPException(400, 'Invalid provider signature') from None


def _facebook_account(subject, issued):
    pool = config.get('cognito_user_pool_id')
    if not pool:
        raise HTTPException(503, 'Provider deletion is not configured')
    client = boto3.client('cognito-idp')
    try:
        user = client.admin_get_user(UserPoolId=pool, Username='Facebook_' + subject)
    except client.exceptions.UserNotFoundException:
        return None
    attributes = {item['Name']: item['Value'] for item in user['UserAttributes']}
    mapping = get_dynamodb_item(f"PROVIDER_USER#{pool}#{attributes['sub']}", 'META', consistent_read=True)
    if mapping and mapping.get('created_at', 0) <= issued:
        return mapping['user_id']
    return None


def _threads_account_page(subject, issued, cursor=None):
    # Existing integrations predate reverse lookup keys. Checkpoint the lookup
    # itself so a large table cannot exceed the worker's execution budget.
    params = {'Limit': 25, 'ConsistentRead': True}
    if cursor:
        params['ExclusiveStartKey'] = cursor
    page = get_dynamodb_table().scan(**params)
    users = {item['pk'].removeprefix('USER#') for item in page.get('Items', [])
             if (item['pk'].startswith('USER#') and item['sk'] == 'INTEGRATION#threads'
                 and item.get('threads_user_id') == subject and item.get('created_at', 0) <= issued)}
    return users, page.get('LastEvaluatedKey')


def accept_meta_request(provider, signed_request):
    subject, issued = verified_meta_request(provider, signed_request)
    dedup = hashlib.sha256((provider + ':' + signed_request).encode()).hexdigest()
    previous = get_dynamodb_item('DELETE_REQUEST#' + dedup, 'META', consistent_read=True)
    if previous:
        # A replay must never resolve the identity again after re-registration.
        from account_deletion_service import dispatch
        dispatch(previous['code'])
        return previous['code']
    from account_deletion_service import request_provider_deletion
    return request_provider_deletion(provider, subject, issued, dedup)
