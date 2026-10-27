"""Provider identities must not become unregistered public application slugs."""
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'web-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import shared_utils
from basic_dtos import UserTokenDTO
from user_dtos import UpdateUserDTO


@pytest.mark.parametrize('provider_username', ['Google_123456789012345678901', 'SignInWithApple_123456789012345678901'])
@pytest.mark.parametrize('provider_match', [True, False])
@pytest.mark.parametrize('saved_username', ['missing', None, '', 'chosen-username'])
def test_authenticated_user_uses_only_saved_application_username(monkeypatch, provider_match, saved_username, provider_username):
    item = {'id': 'example-user', 'name': 'Example Creator', 'created_at': 1}
    if saved_username != 'missing':
        item['username'] = saved_username
    monkeypatch.setattr(shared_utils, 'get_dynamodb_item', Mock(side_effect=[
        {'user_id': item['id']}, item,
    ] if provider_match else [None]))
    monkeypatch.setattr(shared_utils, 'query_dynamodb_table', Mock(return_value={'Items': [item]}))
    token = UserTokenDTO(sub='provider-id', iss='example-provider', email='creator@example.com',
                         username=provider_username, name='Provider Name')

    user = shared_utils.get_user_by_user_token(token)

    if provider_match:
        assert user.id == item['id']
        assert user.username == item.get('username')
        assert user.name == 'Example Creator'
    else:
        assert user is None
    shared_utils.query_dynamodb_table.assert_not_called()


@pytest.mark.parametrize('raw_username', [
    'Google_123456789012345678901', 'A' * 200, 'abcdefghijklmno-' + 'tail' * 20,
    '---First__Last---', 'x',
])
def test_generated_signup_username_passes_profile_validation(raw_username):
    username = shared_utils.build_user_username('Example Creator', raw_username, 1_700_000_000_000)

    assert UpdateUserDTO(username=username).username == username
    assert username.startswith('example-creator-')
    assert username.endswith('-1700000000000')
    assert len(username) <= 30


@pytest.mark.parametrize('name, username', [(None, None), ('你好', None), (None, '___'), (None, 'SignInWithApple_001234.example'), (None, 'Google_123456789')])
def test_signup_can_leave_username_unset_when_no_usable_slug_exists(name, username):
    assert shared_utils.build_user_username(name, username, 1_700_000_000_000) is None


@pytest.mark.parametrize('provider', ['Google', 'SignInWithApple', 'Facebook', 'COGNITO'])
@pytest.mark.parametrize('email', [None, 'same@example.com'])
def test_signin_never_falls_back_to_email_matching(monkeypatch, provider, email):
    read = Mock(return_value=None)
    query = Mock(return_value={'Items': [{'id': 'other-user', 'user_email_pk': email}]})
    monkeypatch.setattr(shared_utils, 'get_dynamodb_item', read)
    monkeypatch.setattr(shared_utils, 'query_dynamodb_table', query)
    token = UserTokenDTO(sub=provider + '-sub', iss='https://issuer.example/pool', email=email)
    assert shared_utils.get_user_by_user_token(token) is None
    read.assert_called_once_with('PROVIDER_USER#pool#' + provider + '-sub', 'META')
    query.assert_not_called()


@pytest.mark.parametrize('issuer', ['https://issuer.example/pool', 'example-provider'])
@pytest.mark.parametrize('email', [None, 'same@example.com'])
def test_session_cookie_retains_original_provider_identity(monkeypatch, issuer, email):
    from web_utils import create_auth_jwt_token

    monkeypatch.setitem(shared_utils.get_config(), 'auth_jwt_secret', 'test-session-secret')
    token = UserTokenDTO(sub='provider-sub', iss=issuer, email=email)
    session = shared_utils.get_user_token_by_auth_jwt_token(create_auth_jwt_token(token))
    assert session.iss == token.iss and session.sub == token.sub
    record = {'id': 'saved-user', 'name': 'Saved Creator', 'created_at': 1}
    read = Mock(side_effect=[{'user_id': record['id']}, record])
    monkeypatch.setattr(shared_utils, 'get_dynamodb_item', read)
    query = Mock()
    monkeypatch.setattr(shared_utils, 'query_dynamodb_table', query)
    assert shared_utils.get_user_by_user_token(session).id == 'saved-user'
    assert read.call_args_list[0].args == ('PROVIDER_USER#' + issuer.split('/')[-1] + '#provider-sub', 'META')
    query.assert_not_called()


def test_deleted_provider_target_cannot_fall_back_to_another_account(monkeypatch):
    monkeypatch.setattr(shared_utils, 'get_dynamodb_item', Mock(side_effect=[{'user_id': 'deleted-user'}, None]))
    query = Mock()
    monkeypatch.setattr(shared_utils, 'query_dynamodb_table', query)
    assert shared_utils.get_user_by_user_token(UserTokenDTO(sub='known-sub', iss='issuer', email='same@example.com')) is None
    query.assert_not_called()
