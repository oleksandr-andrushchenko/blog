"""Opt-in public provider identities never require a public-page connection read."""
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda', 'web-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import api_utils
import integration_visibility
from creator_integrations import creator_integration_links
from integration_profiles import VISIBILITY_FIELDS
from shared_utils import user_from_dynamodb, DynamoDBTransactionError
from user_dtos import UpdateUserDTO
from web import HTTPException


def record(**changes):
    return {'id': 'creator', 'name': 'Creator', 'created_at': 1,
            'has_telegram_integration': True, 'has_threads_integration': True,
            'telegram_public_profile': {'title': '<Channel>', 'username': 'example_channel', 'bot_token': 'secret'},
            'threads_public_profile': {'username': 'example.creator', 'encrypted_token': 'secret'}, **changes}


@pytest.mark.parametrize('field', VISIBILITY_FIELDS)
@pytest.mark.parametrize('invalid', [None, 0, 1, 'true', [], {}])
def test_visibility_requires_json_booleans(field, invalid):
    with pytest.raises(ValueError):
        UpdateUserDTO(**{field: invalid})
    assert UpdateUserDTO(**{field: False}).get_changes() == {field: False}


def test_legacy_connections_remain_private():
    user = user_from_dynamodb(record())
    assert creator_integration_links(user, 'user_page') == []
    assert creator_integration_links(user, 'articles_pages') == []
    assert user.telegram_public_profile is None
    assert user.threads_public_profile is None


def test_placement_preferences_and_public_identity_allowlist():
    user = user_from_dynamodb(record(telegram_show_on_user_page=True, threads_show_on_articles_pages=True))
    assert user.telegram_public_profile == {'title': '<Channel>', 'username': 'example_channel'}
    assert user.threads_public_profile == {'username': 'example.creator'}
    assert creator_integration_links(user, 'user_page') == [
        {'icon': 'bi-telegram', 'provider': 'Telegram', 'url': 'https://t.me/example_channel', 'label': '<Channel>'}]
    assert creator_integration_links(user, 'articles_pages') == [
        {'icon': 'bi-threads', 'provider': 'Threads', 'url': 'https://www.threads.com/@example.creator', 'label': '@example.creator'}]
    disconnected = user_from_dynamodb(record(has_telegram_integration=False, telegram_show_on_user_page=True))
    assert creator_integration_links(disconnected, 'user_page') == []


def test_private_channel_has_name_without_private_link_and_fragment_escapes_html():
    user = user_from_dynamodb(record(telegram_show_on_user_page=True,
                                    telegram_public_profile={'title': '<Private channel>', 'channel_id': '-100123'}))
    links = creator_integration_links(user, 'user_page')
    assert links[0]['url'] is None
    env = Environment(loader=FileSystemLoader(str(ROOT / 'web-lambda/templates')), autoescape=select_autoescape())
    template = env.get_template('fragments/creator-integrations.html')
    html = template.render(integration_links=links)
    assert '&lt;Private channel&gt;' in html
    assert 'href=' not in html and '-100123' not in html
    assert not template.render(integration_links=[]).strip()
    public_html = template.render(integration_links=creator_integration_links(
        user_from_dynamodb(record(threads_show_on_user_page=True)), 'user_page'))
    assert 'target="_blank"' in public_html and 'rel="nofollow noopener noreferrer"' in public_html


@pytest.mark.parametrize('username', ['../secret', 'example?token=x', '//unrelated.example', 'example#foo', '..'])
def test_invalid_account_names_cannot_create_public_links(username):
    user = user_from_dynamodb(record(threads_show_on_user_page=True, threads_public_profile={'username': username}))
    assert creator_integration_links(user, 'user_page') == []


@pytest.mark.parametrize('field', VISIBILITY_FIELDS)
def test_other_profile_editors_cannot_change_visibility(monkeypatch, field):
    user = user_from_dynamodb(record())
    admin = user_from_dynamodb(record(id='administrator'))
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    write = Mock()
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', write)
    with pytest.raises(HTTPException) as exc:
        api_utils.update_user(user, UpdateUserDTO(**{field: False}), admin, None)
    assert exc.value.status_code == 403
    write.assert_not_called()


@pytest.mark.parametrize('provider', ['telegram', 'threads'])
def test_legacy_identity_hydration_is_transactional_and_safe(monkeypatch, provider):
    connection = {'channel_title': 'Example channel', 'channel_username': 'example_channel', 'channel_id': '-1001',
                  'username': 'example.creator', 'session_hash': 'receipt', 'encrypted_token': 'secret'}
    monkeypatch.setattr(integration_visibility, 'get_dynamodb_item', Mock(return_value=connection))
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    monkeypatch.setattr(api_utils, 'dispatch_profile_sync', Mock())
    write = Mock()
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', write)
    user = user_from_dynamodb(record())
    api_utils.update_user(user, UpdateUserDTO(**{f'{provider}_show_on_user_page': True}), user, None)
    transaction = write.call_args.args[0]
    guard = transaction[0]['ConditionCheck']
    assert guard['Key']['sk'] == 'INTEGRATION#' + provider
    assert 'attribute_exists(pk)' in guard['ConditionExpression']
    update = next(item['Update'] for item in transaction if 'Update' in item)
    profile = update['ExpressionAttributeValues'][f':new_{provider}_public_profile']
    assert profile == ({'title': 'Example channel', 'username': 'example_channel'} if provider == 'telegram'
                       else {'username': 'example.creator'})
    assert 'secret' not in str(transaction)
    assert 'attribute_not_exists(deletion_id)' in update['ConditionExpression']


def test_connection_race_returns_recoverable_error(monkeypatch):
    monkeypatch.setattr(integration_visibility, 'get_dynamodb_item', Mock(return_value=None))
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', Mock(side_effect=DynamoDBTransactionError('ConditionalCheckFailed')))
    user = user_from_dynamodb(record())
    with pytest.raises(HTTPException) as exc:
        api_utils.update_user(user, UpdateUserDTO(threads_show_on_user_page=True), user, None)
    assert exc.value.status_code == 409


@pytest.mark.parametrize('provider', ['telegram', 'threads'])
def test_checkboxes_use_saved_preferences_in_existing_profile_form(provider):
    env = Environment(loader=FileSystemLoader(str(ROOT / 'web-lambda/templates')), autoescape=select_autoescape())
    # Provider fragments also contain scripts requiring URL and tojson; use their markup portion.
    source = (ROOT / f'web-lambda/templates/fragments/{provider}-integration.html').read_text().split('<script>')[0]
    user = user_from_dynamodb(record(**{f'{provider}_show_on_user_page': True}))
    html = env.from_string(source).render(user=user, **{f'{provider}_publisher_enabled': True})
    from pyquery import PyQuery as pq
    document = pq(html)
    assert document(f'input[name="{provider}_show_on_user_page"]').is_(':checked')
    assert not document(f'input[name="{provider}_show_on_articles_pages"]').is_(':checked')
    assert len(document('input[type=checkbox][disabled]')) == 2
    assert len(document('form')) == 0
