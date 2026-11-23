"""Pinterest publishing uses owner-bound OAuth and never repeats uncertain writes."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography.fernet import Fernet
import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import shared_utils
import pinterest_service as service
from shared_utils import user_from_dynamodb
from web import HTTPException


@pytest.fixture
def actor():
    return SimpleNamespace(id='pin-' + str(uuid.uuid4()), status='active')


@pytest.fixture
def provider(monkeypatch):
    settings = {'pinterest_app_id': 'synthetic-app', 'pinterest_app_secret': 'synthetic-secret',
                'pinterest_token_encryption_key': Fernet.generate_key().decode(),
                'api_base_url': 'https://api.catalog.example', 'web_base_url': 'https://catalog.example'}
    monkeypatch.setattr(service, 'config', settings)
    remember = Mock(side_effect=lambda article, platform, result, **kwargs: result)
    monkeypatch.setattr(service, 'remember_article_share', remember)
    monkeypatch.setattr(service, 'get_article_url', lambda *args, **kwargs: 'https://catalog.example/articles/example')

    def call(path, **params):
        if path == '/oauth/token':
            suffix = 'refreshed' if params['form']['grant_type'] == 'refresh_token' else 'original'
            return {'access_token': 'private-access-' + suffix, 'refresh_token': 'private-refresh-' + suffix,
                    'expires_in': 2592000, 'refresh_token_expires_in': 5184000,
                    'scope': 'user_accounts:read boards:read pins:write'}
        if path == '/user_account':
            return {'id': '12345', 'username': 'example_creator'}
        if path == '/boards':
            if params.get('bookmark'):
                return {'items': [{'id': '12', 'name': 'Other board', 'privacy': 'PUBLIC',
                                   'owner': {'username': 'someone_else'}}], 'bookmark': None}
            return {'items': [{'id': '11', 'name': 'Example board', 'privacy': 'PUBLIC',
                               'owner': {'username': 'example_creator'}}], 'bookmark': 'next'}
        if path.startswith('/boards/'):
            return {'id': path.rsplit('/', 1)[1], 'name': 'Example board', 'privacy': 'PUBLIC',
                    'owner': {'username': 'example_creator'}}
        if path == '/pins':
            return {'id': '987654321'}
        raise AssertionError(path)

    call_mock = Mock(side_effect=call)
    monkeypatch.setattr(service, 'pinterest_call', call_mock)
    return call_mock, remember, settings


@pytest.fixture
def storage(monkeypatch, actor):
    from test_utils import dynamodb_table, recreate_dynamodb_table
    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    dynamodb_table.put_item(Item={**key, 'id': actor.id, 'name': 'Creator', 'created_at': 1, 'status': 'active'})
    article_id = 'example-' + actor.id
    dynamodb_table.put_item(Item={'pk': 'POST#' + article_id, 'sk': 'META', 'id': article_id,
                                  'user_id': actor.id, 'status': 'published'})
    yield dynamodb_table
    from boto3.dynamodb.conditions import Key
    for item in dynamodb_table.query(KeyConditionExpression=Key('pk').eq(key['pk']))['Items']:
        dynamodb_table.delete_item(Key={'pk': item['pk'], 'sk': item['sk']})
    dynamodb_table.delete_item(Key={'pk': 'POST#' + article_id, 'sk': 'META'})


def begin(actor):
    result, binding = service.begin_connection(actor, actor)
    query = parse_qs(urlsplit(result['connect_url']).query)
    assert set(query['scope'][0].split(',')) == {'user_accounts:read', 'boards:read', 'pins:write'}
    return {'state': query['state'][0], 'binding': binding, 'code': 'private-single-use-code'}


def connect(actor):
    attempt = begin(actor)
    assert service.complete_connection(actor, **attempt)
    return attempt


def article(actor, **changes):
    return SimpleNamespace(**{'id': 'example-' + actor.id, 'owner_id': actor.id, 'status': 'published',
                             'title': 'Example article', 'preview': 'Example description.',
                             'image_filename': 'https://static.catalog.example/result.png',
                             **changes})


def test_connect_encrypts_tokens_and_caches_only_public_identity(actor, provider, storage):
    attempt = connect(actor)
    connection = service._read(actor.id, service.INTEGRATION_KEY)
    assert 'private-access' not in str(connection) and 'private-refresh' not in str(connection)
    assert 'private-single-use-code' not in str(connection)
    payload = json.loads(service._cipher().decrypt(connection['encrypted_token'].encode()))
    assert payload['user_id'] == actor.id and payload['pinterest_user_id'] == '12345'
    user = service._read(actor.id, 'META')
    assert user['pinterest_public_profile'] == {'username': 'example_creator'}
    assert user['has_pinterest_integration'] is True
    assert user_from_dynamodb(user).pinterest_public_profile is None  # Opt-in only.
    assert service.connection_status(actor, actor)['username'] == 'example_creator'
    with pytest.raises(HTTPException):
        service.complete_connection(actor, **attempt)


@pytest.mark.parametrize('changed', ['state', 'binding'])
def test_browser_and_state_are_required(actor, provider, storage, changed):
    attempt = begin(actor)
    attempt[changed] = 'wrong'
    with pytest.raises(HTTPException) as exc:
        service.complete_connection(actor, **attempt)
    assert exc.value.status_code == 409
    provider[0].assert_not_called()


@pytest.mark.parametrize('cancel', ['disconnect', 'replace', 'denied'])
def test_cancelled_callback_cannot_replace_connection(actor, provider, storage, cancel):
    attempt = begin(actor)
    if cancel == 'denied':
        assert service.complete_connection(actor, **attempt, denied=True) is False
        provider[0].assert_not_called()
    else:
        if cancel == 'disconnect':
            service.disconnect(actor, actor)
        else:
            begin(actor)
        with pytest.raises(HTTPException):
            service.complete_connection(actor, **attempt)
    assert service._read(actor.id, service.INTEGRATION_KEY) is None


def test_disconnect_during_callback_cannot_resurrect_connection(actor, provider, storage):
    attempt = begin(actor)
    call = provider[0].side_effect
    def disconnecting(path, **params):
        result = call(path, **params)
        if path == '/user_account':
            service.disconnect(actor, actor)
        return result
    provider[0].side_effect = disconnecting
    with pytest.raises(HTTPException) as exc:
        service.complete_connection(actor, **attempt)
    assert exc.value.status_code == 409
    assert service._read(actor.id, service.INTEGRATION_KEY) is None
    assert 'pinterest_public_profile' not in service._read(actor.id, 'META')


@pytest.mark.parametrize('operation', ['status', 'begin', 'disconnect', 'boards', 'select'])
def test_only_owner_can_manage_even_if_other_editor_is_admin(actor, provider, storage, operation):
    admin = SimpleNamespace(id='admin', status='active', permissions=['root'])
    methods = {'status': service.connection_status, 'begin': service.begin_connection,
               'disconnect': service.disconnect, 'boards': service.list_boards,
               'select': lambda user, editor: service.select_board(user, editor, '11')}
    with pytest.raises(HTTPException) as exc:
        methods[operation](actor, admin)
    assert exc.value.status_code == 403
    provider[0].assert_not_called()


def test_boards_are_paginated_filtered_and_saved_privately(actor, provider, storage):
    connect(actor)
    assert service.list_boards(actor, actor) == {'boards': [{'id': '11', 'name': 'Example board'}], 'board_id': None}
    assert service.select_board(actor, actor, '11') == {'saved': True, 'board_id': '11'}
    assert service._read(actor.id, service.INTEGRATION_KEY)['board_id'] == '11'
    assert 'board_id' not in service._read(actor.id, 'META')
    with pytest.raises(HTTPException):
        service.select_board(actor, actor, '../bad')


def test_refresh_rotates_encrypted_tokens_and_preserves_board(actor, provider, storage):
    connect(actor)
    service.select_board(actor, actor, '11')
    storage.update_item(Key={'pk': 'USER#' + actor.id, 'sk': service.INTEGRATION_KEY},
                        UpdateExpression='SET expires_at = :expired', ExpressionAttributeValues={':expired': 1})
    token, record = service._access_token(actor)
    assert token == 'private-access-refreshed'
    assert record['board_id'] == '11'
    assert 'private-refresh' not in str(service._read(actor.id, service.INTEGRATION_KEY))


def test_refresh_cannot_undo_disconnect(actor, provider, storage):
    connect(actor)
    storage.update_item(Key={'pk': 'USER#' + actor.id, 'sk': service.INTEGRATION_KEY},
                        UpdateExpression='SET expires_at = :expired', ExpressionAttributeValues={':expired': 1})
    call = provider[0].side_effect
    def disconnecting(path, **params):
        result = call(path, **params)
        if path == '/oauth/token':
            service.disconnect(actor, actor)
        return result
    provider[0].side_effect = disconnecting
    with pytest.raises(HTTPException):
        service._access_token(actor)
    assert service._read(actor.id, service.INTEGRATION_KEY) is None


def test_create_pin_links_to_article_and_same_event_never_repeats_remote_write(actor, provider, storage):
    connect(actor)
    service.select_board(actor, actor, '11')
    event = str(uuid.uuid4())
    first = service.share_article(article(actor), actor, None, event)
    second = service.share_article(article(actor), actor, None, event)
    assert first == second and first['shared'] is True
    writes = [call for call in provider[0].call_args_list if call.args[0] == '/pins']
    assert len(writes) == 1
    payload = writes[0].kwargs['payload']
    assert payload['link'] == 'https://catalog.example/articles/example'
    assert payload['board_id'] == '11' and payload['description'] == 'Example description.'
    assert payload['media_source']['source_type'] == 'image_url'
    assert provider[1].call_args.kwargs['event_id'] == '987654321'


@pytest.mark.parametrize('failure', ['timeout', 'malformed'])
def test_uncertain_remote_write_is_not_repeated(actor, provider, storage, failure):
    connect(actor)
    service.select_board(actor, actor, '11')
    call = provider[0].side_effect
    def uncertain(path, **params):
        if path == '/pins':
            if failure == 'timeout':
                raise service.PinterestRequestUnconfirmed(502, 'Unknown result')
            return {}
        return call(path, **params)
    provider[0].side_effect = uncertain
    event = str(uuid.uuid4())
    for _ in range(2):
        result = service.share_article(article(actor), actor, None, event)
        assert result['status'] == 'unconfirmed' and not result['shared']
    assert len([call for call in provider[0].call_args_list if call.args[0] == '/pins']) == 1
    provider[1].assert_not_called()


@pytest.mark.parametrize('case', ['unpublished', 'other-owner', 'no-image', 'no-board', 'bad-event'])
def test_publish_guards_precede_remote_creation(actor, provider, storage, case):
    connect(actor)
    if case != 'no-board':
        service.select_board(actor, actor, '11')
    changes = {'unpublished': {'status': 'unpublished'}, 'other-owner': {'owner_id': 'other'},
               'no-image': {'image_filename': None}}.get(case, {})
    with pytest.raises(HTTPException):
        service.share_article(article(actor, **changes), actor, None, 'bad' if case == 'bad-event' else str(uuid.uuid4()))
    assert not any(call.args[0] == '/pins' for call in provider[0].call_args_list)
