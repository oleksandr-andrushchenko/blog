"""Provider identities must not become unregistered public application slugs."""
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'shared'))

import shared_utils
from basic_dtos import UserTokenDTO
from user_dtos import UpdateUserDTO


@pytest.mark.parametrize('provider_match', [True, False])
@pytest.mark.parametrize('saved_username', ['missing', None, '', 'chosen-username'])
def test_authenticated_user_uses_only_saved_application_username(monkeypatch, provider_match, saved_username):
    item = {'id': 'example-user', 'name': 'Example Creator', 'created_at': 1}
    if saved_username != 'missing':
        item['username'] = saved_username
    monkeypatch.setattr(shared_utils, 'get_dynamodb_item', Mock(side_effect=[
        {'user_id': item['id']}, item,
    ] if provider_match else [None]))
    monkeypatch.setattr(shared_utils, 'query_dynamodb_table', Mock(return_value={'Items': [item]}))
    token = UserTokenDTO(sub='provider-id', iss='example-provider', email='creator@example.com',
                         username='Google_123456789012345678901', name='Provider Name')

    user = shared_utils.get_user_by_user_token(token)

    assert user.id == item['id']
    assert user.username == item.get('username')
    assert user.name == 'Example Creator'


@pytest.mark.parametrize('raw_username', [
    'Google_123456789012345678901', 'A' * 200, 'abcdefghijklmno-' + 'tail' * 20,
    '---First__Last---', 'x',
])
def test_generated_signup_username_passes_profile_validation(raw_username):
    username = shared_utils.build_user_username('Example Creator', raw_username, 1_700_000_000_000)

    assert UpdateUserDTO(username=username).username == username
    assert username.endswith('-1700000000000')
    assert len(username) <= 30


@pytest.mark.parametrize('name, username', [(None, None), ('你好', None), (None, '___')])
def test_signup_can_leave_username_unset_when_no_usable_slug_exists(name, username):
    assert shared_utils.build_user_username(name, username, 1_700_000_000_000) is None
