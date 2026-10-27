"""Explicit login connections bind both account proofs and preserve existing ownership."""
import hashlib
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock
import uuid

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import login_link_service as service
import shared_utils
from basic_dtos import UserTokenDTO
from shared_utils import encode_offset, user_from_dynamodb
from web import HTTPException, Request


def request(*, state='', binding='', session='original-session', code='', error=''):
    query = {'state': state, 'code': code}
    if error:
        query['error'] = error
    return Request({'type': 'http', 'method': 'GET', 'path': '/auth/login-link/callback',
                    'query_string': urlencode(query).encode(), 'scheme': 'https',
                    'server': ('api.example.com', 443),
                    'headers': [(b'cookie', f'token={session}; {service.COOKIE_NAME}={binding}'.encode())]})


@pytest.fixture
def account(monkeypatch):
    from test_utils import dynamodb_table, recreate_dynamodb_table

    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(service, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    monkeypatch.setattr(service, 'get_url', lambda *args, **kwargs: 'https://api.example.com/auth/login-link/callback')
    monkeypatch.setattr(service, 'is_prod', lambda: False)
    identifier = 'login-link-' + uuid.uuid4().hex
    item = {'pk': f'USER#{identifier}', 'sk': 'META', 'id': identifier, 'name': 'Original Creator',
            'user_email_pk': identifier + '@example.com', 'status': 'active', 'created_at': 1, 'updated_at': 2}
    dynamodb_table.put_item(Item=item)
    actor = user_from_dynamodb(item)
    yield dynamodb_table, actor, item
    dynamodb_table.delete_item(Key={'pk': item['pk'], 'sk': 'META'})
    dynamodb_table.delete_item(Key={'pk': item['pk'], 'sk': service.PENDING_KEY})


def begin(actor):
    result, binding = service.begin_connection(actor, actor, request())
    params = parse_qs(urlsplit(result['connect_url']).query)
    return params['state'][0], binding, params


def identity(actor):
    return UserTokenDTO(sub='secondary-' + actor.id, iss='https://issuer.example/pool',
                         name='Different Provider Name', email='different@example.com')


def test_connection_maps_an_unassigned_identity_without_changing_profile(account, monkeypatch):
    table, actor, original = account
    proof = identity(actor)
    exchange = Mock(return_value=proof)
    monkeypatch.setattr(service, 'exchange_login_code', exchange)
    state, binding, params = begin(actor)
    assert params['code_challenge_method'] == ['S256'] and params['nonce'] == [state]
    pending = table.get_item(Key={'pk': original['pk'], 'sk': service.PENDING_KEY}, ConsistentRead=True)['Item']
    assert pending['auth_hash'] == hashlib.sha256(b'original-session').hexdigest()
    assert 'original-session' not in pending.values() and state not in pending.values()
    provider_key = {'pk': f'PROVIDER_USER#pool#{proof.sub}', 'sk': 'META'}
    try:
        assert service.complete_connection(actor, request(state=state, binding=binding, code='single-use')) == 'connected'
        exchange.assert_called_once_with('single-use', 'https://api.example.com/auth/login-link/callback',
                                          verifier=pending['verifier'], nonce=state)
        saved = table.get_item(Key=provider_key, ConsistentRead=True)['Item']
        assert saved['user_id'] == actor.id
        assert set(saved) == {'pk', 'sk', 'user_id', 'created_at'}
        assert table.get_item(Key={'pk': original['pk'], 'sk': 'META'}, ConsistentRead=True)['Item'] == original
        assert shared_utils.get_user_by_user_token(proof).id == actor.id
        assert 'Item' not in table.get_item(Key={'pk': original['pk'], 'sk': service.PENDING_KEY})
        with pytest.raises(service.LoginLinkError):
            service.complete_connection(actor, request(state=state, binding=binding, code='single-use'))
        assert exchange.call_count == 1
        state, binding, _ = begin(actor)
        assert service.complete_connection(actor, request(state=state, binding=binding, code='another-code')) == 'already_connected'
    finally:
        table.delete_item(Key=provider_key)


@pytest.mark.parametrize('invalid', ['state', 'binding', 'session', 'expired'])
def test_connection_rejects_unbound_or_expired_callbacks_before_exchange(account, monkeypatch, invalid):
    table, actor, original = account
    state, binding, _ = begin(actor)
    session = 'original-session'
    if invalid == 'state':
        state = 'wrong'
    elif invalid == 'binding':
        binding = 'wrong'
    elif invalid == 'session':
        session = 'different-session'
    else:
        table.update_item(Key={'pk': original['pk'], 'sk': service.PENDING_KEY},
                          UpdateExpression='SET expires_at = :expired', ExpressionAttributeValues={':expired': 1})
    exchange = Mock()
    monkeypatch.setattr(service, 'exchange_login_code', exchange)
    with pytest.raises(service.LoginLinkError):
        service.complete_connection(actor, request(state=state, binding=binding, session=session, code='code'))
    exchange.assert_not_called()


def test_identity_already_assigned_to_another_account_cannot_be_taken(account, monkeypatch):
    table, actor, original = account
    proof = identity(actor)
    key = {'pk': f'PROVIDER_USER#pool#{proof.sub}', 'sk': 'META'}
    assigned = {**key, 'user_id': 'another-account', 'created_at': 1}
    table.put_item(Item=assigned)
    monkeypatch.setattr(service, 'exchange_login_code', Mock(return_value=proof))
    state, binding, _ = begin(actor)
    try:
        with pytest.raises(service.LoginLinkError) as error:
            service.complete_connection(actor, request(state=state, binding=binding, code='code'))
        assert error.value.reason == 'in_use'
        assert table.get_item(Key=key, ConsistentRead=True)['Item'] == assigned
        assert table.get_item(Key={'pk': original['pk'], 'sk': 'META'}, ConsistentRead=True)['Item'] == original
    finally:
        table.delete_item(Key=key)


@pytest.mark.parametrize('change', ['new-attempt', 'banned'])
def test_replacement_or_ban_during_code_exchange_cancels_association(account, monkeypatch, change):
    table, actor, original = account
    proof = identity(actor)
    state, binding, _ = begin(actor)

    def exchange(*args, **kwargs):
        if change == 'new-attempt':
            begin(actor)
        else:
            table.update_item(Key={'pk': original['pk'], 'sk': 'META'}, UpdateExpression='SET #status = :status',
                              ExpressionAttributeNames={'#status': 'status'}, ExpressionAttributeValues={':status': 'banned'})
        return proof

    monkeypatch.setattr(service, 'exchange_login_code', exchange)
    with pytest.raises(service.LoginLinkError):
        service.complete_connection(actor, request(state=state, binding=binding, code='code'))
    assert 'Item' not in table.get_item(Key={'pk': f'PROVIDER_USER#pool#{proof.sub}', 'sk': 'META'}, ConsistentRead=True)


def test_denied_callback_consumes_pending_without_exchanging_code(account, monkeypatch):
    table, actor, original = account
    state, binding, _ = begin(actor)
    exchange = Mock()
    monkeypatch.setattr(service, 'exchange_login_code', exchange)
    assert service.complete_connection(actor, request(state=state, binding=binding, error='access_denied')) == 'denied'
    exchange.assert_not_called()
    assert 'Item' not in table.get_item(Key={'pk': original['pk'], 'sk': service.PENDING_KEY})


def test_failed_code_exchange_cannot_be_replayed(account, monkeypatch):
    _, actor, _ = account
    state, binding, _ = begin(actor)
    exchange = Mock(side_effect=shared_utils.InvalidTokenError('Invalid proof'))
    monkeypatch.setattr(service, 'exchange_login_code', exchange)
    with pytest.raises(shared_utils.InvalidTokenError):
        service.complete_connection(actor, request(state=state, binding=binding, code='code'))
    with pytest.raises(service.LoginLinkError):
        service.complete_connection(actor, request(state=state, binding=binding, code='code'))
    assert exchange.call_count == 1


def test_only_active_owner_can_start_connection(account):
    _, actor, original = account
    other = user_from_dynamodb({**original, 'id': 'other-user'})
    with pytest.raises(HTTPException) as error:
        service.begin_connection(actor, other, request())
    assert error.value.status_code == 403
    actor.status = 'banned'
    with pytest.raises(HTTPException):
        service.begin_connection(actor, actor, request())


def test_concurrent_signup_cannot_overwrite_an_explicit_connection(account, monkeypatch):
    table, actor, _ = account
    proof = identity(actor)
    provider_key = {'pk': f'PROVIDER_USER#pool#{proof.sub}', 'sk': 'META'}
    original_write = shared_utils.dynamodb_transact_write
    created_user_keys = []

    def connect_before_signup_commit(transacts):
        for operation in transacts:
            item = operation.get('Put', {}).get('Item', {})
            if item.get('pk', '').startswith('USER#') and item.get('sk') == 'META':
                created_user_keys.append({'pk': item['pk'], 'sk': item['sk']})
        table.put_item(Item={**provider_key, 'user_id': actor.id, 'created_at': 1})
        original_write(transacts)

    monkeypatch.setattr(shared_utils, 'dynamodb_transact_write', connect_before_signup_commit)
    try:
        assert shared_utils.upsert_user_by_user_token(proof, req=request()).id == actor.id
        assert table.get_item(Key=provider_key, ConsistentRead=True)['Item']['user_id'] == actor.id
        assert created_user_keys
        assert all('Item' not in table.get_item(Key=key, ConsistentRead=True) for key in created_user_keys)
    finally:
        table.delete_item(Key=provider_key)
