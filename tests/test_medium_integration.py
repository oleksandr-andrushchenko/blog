"""Medium connections protect credentials and export only public previews."""
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
import uuid

from cryptography.fernet import Fernet
import pytest
from pyquery import PyQuery

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import article_sharing
import medium_service as service
import shared_utils
from web import HTTPException

TOKEN = 'synthetic-medium-token'
transport = service.medium_call


@pytest.fixture
def setup(monkeypatch):
    from test_utils import dynamodb_table, recreate_dynamodb_table
    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    actor = SimpleNamespace(id='medium-test-' + str(uuid.uuid4()), status='active')
    key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    dynamodb_table.put_item(Item={**key, 'id': actor.id, 'name': 'Example', 'created_at': 1})
    config = {'medium_token_encryption_key': Fernet.generate_key().decode()}
    monkeypatch.setattr(service, 'get_config', lambda: config)
    with monkeypatch.context() as templates:
        templates.setattr(shared_utils, 'get_function_templates_dir', lambda: str(ROOT / 'api-lambda/templates'))
        environment = shared_utils.get_jinja2_env()
    monkeypatch.setattr(service, 'jinja2_env', lambda: environment)
    monkeypatch.setattr(service, 'find_category', lambda slug: SimpleNamespace(name='System Design'))
    monkeypatch.setattr(service, 'get_article_url', lambda *a, **k: 'https://site.example/article?a=1&b=2')
    monkeypatch.setattr(article_sharing, 'get_static_url', lambda *a, **k: 'https://static.example/cover.webp')
    history = Mock(return_value=True)
    monkeypatch.setattr(article_sharing, 'record_article_share', history)
    call = Mock(side_effect=lambda path, token, **kw: {'id': 'author', 'username': 'creator'} if path == '/me' else
                {'id': 'post-123', 'authorId': 'author', 'publishStatus': 'public',
                 'url': 'https://medium.com/@creator/example'})
    monkeypatch.setattr(service, 'medium_call', call)
    article = SimpleNamespace(id='example', owner_id=actor.id, status='published', title='Design <systems>',
                              category='system-design', preview='<p>A &amp; B &lt;safe&gt;</p>',
                              image_filename='cover.webp', content='PRIVATE BODY NEVER EXPORT', is_premium=True)
    yield SimpleNamespace(actor=actor, article=article, config=config, call=call, history=history, table=dynamodb_table)
    for sk in ('META', service.INTEGRATION_KEY):
        dynamodb_table.delete_item(Key={**key, 'sk': sk})


def connect(state):
    return service.connect(state.actor, state.actor, {'token': TOKEN})


def test_connection_encrypts_token_and_disconnects_atomically(setup):
    state = setup
    assert service.connection_status(state.actor, state.actor) == {'configured': True, 'connected': False}
    assert connect(state) == {'connected': True, 'username': 'creator'}
    saved = service._read(state.actor.id)
    assert TOKEN not in str(saved)
    assert service._credentials(state.actor.id, saved) == TOKEN
    assert TOKEN not in str(service.connection_status(state.actor, state.actor))
    user = shared_utils.get_dynamodb_item('USER#' + state.actor.id, 'META')
    assert shared_utils.user_from_dynamodb(user).has_medium_integration
    service.disconnect(state.actor, state.actor)
    assert service._read(state.actor.id) is None
    assert not shared_utils.user_from_dynamodb(shared_utils.get_dynamodb_item('USER#' + state.actor.id, 'META')).has_medium_integration


@pytest.mark.parametrize('action', ['status', 'connect', 'disconnect', 'share'])
@pytest.mark.parametrize('reason', ['other-owner', 'inactive'])
def test_owner_and_active_account_required(setup, action, reason):
    state = setup
    target = SimpleNamespace(id='someone-else' if reason == 'other-owner' else state.actor.id)
    if reason == 'inactive':
        state.actor.status = 'banned'
    state.article.owner_id = target.id
    operations = {
        'status': lambda: service.connection_status(target, state.actor),
        'connect': lambda: service.connect(target, state.actor, {'token': TOKEN}),
        'disconnect': lambda: service.disconnect(target, state.actor),
        'share': lambda: service.share_article(state.article, state.actor, None),
    }
    with pytest.raises(HTTPException) as error:
        operations[action]()
    assert error.value.status_code == 403
    state.call.assert_not_called()


@pytest.mark.parametrize('payload', [None, [], {}, {'token': ''}, {'token': 'x\ny'}, {'token': 'x\x00y'}, {'token': 'x' * 4097}])
def test_invalid_tokens_are_rejected_before_provider_request(setup, payload):
    with pytest.raises(HTTPException) as error:
        service.connect(setup.actor, setup.actor, payload)
    assert error.value.status_code == 422
    setup.call.assert_not_called()


def test_short_post_escapes_preview_and_keeps_canonical_and_cover(setup):
    state = setup
    connect(state)
    result = service.share_article(state.article, state.actor, None)
    assert result['shared'] is True
    payload = state.call.call_args.kwargs['payload']
    assert payload['publishStatus'] == 'public'
    assert payload['canonicalUrl'] == 'https://site.example/article?a=1&b=2'
    assert payload['contentFormat'] == 'html'
    assert 'PRIVATE BODY' not in json.dumps(payload)
    doc = PyQuery(payload['content'])
    assert doc('h1').text() == 'Design <systems>'
    assert doc('img').attr('src') == 'https://static.example/cover.webp'
    assert doc('a').attr('href') == payload['canonicalUrl']
    assert 'A & B <safe>' in doc.text()
    assert not doc('safe')
    assert state.article.medium_shared_at
    assert state.history.call_args.args[2] == 'medium_publish'


@pytest.mark.parametrize('case', ['draft', 'disconnected', 'changed-key', 'wrong-identity'])
def test_invalid_share_state_never_posts(setup, case):
    state = setup
    connect(state)
    if case == 'draft':
        state.article.status = 'draft'
    elif case == 'disconnected':
        service.disconnect(state.actor, state.actor)
    elif case == 'changed-key':
        state.config['medium_token_encryption_key'] = Fernet.generate_key().decode()
        assert service.connection_status(state.actor, state.actor)['reconnect_required']
    else:
        record = service._read(state.actor.id)
        record['medium_user_id'] = 'someone-else'
        state.table.put_item(Item=record)
    state.call.reset_mock()
    with pytest.raises(HTTPException):
        service.share_article(state.article, state.actor, None)
    state.call.assert_not_called()


@pytest.mark.parametrize('response', [None, {}, {'id': 'post', 'publishStatus': 'draft', 'authorId': 'author'},
                                     {'id': 'post', 'publishStatus': 'public', 'authorId': 'someone-else'}])
def test_unconfirmed_shares_do_not_write_history_or_retry(setup, response):
    state = setup
    connect(state)
    state.call.reset_mock()
    if response is None:
        state.call.side_effect = service.MediumRequestUnconfirmed(502, 'Uncertain')
    else:
        state.call.side_effect = None
        state.call.return_value = response
    assert service.share_article(state.article, state.actor, None) == {'shared': False, 'status': 'unconfirmed'}
    assert state.call.call_count == 1
    state.history.assert_not_called()


def test_connection_changed_during_token_validation_is_not_overwritten(setup):
    state = setup
    connect(state)
    def race(*args, **kwargs):
        service.disconnect(state.actor, state.actor)
        return {'id': 'author', 'username': 'creator'}
    state.call.side_effect = race
    with pytest.raises(HTTPException) as error:
        connect(state)
    assert error.value.status_code == 409
    assert service._read(state.actor.id) is None


@pytest.mark.parametrize('failure,code', [(401, 409), (403, 409), (429, 429), (400, 422), (500, 502), ('timeout', 502), ('json', 502)])
def test_transport_redacts_errors_and_never_retries(monkeypatch, failure, code):
    if failure == 'timeout':
        error = URLError('secret ' + TOKEN)
    elif failure == 'json':
        error = ValueError('secret ' + TOKEN)
    else:
        error = HTTPError('https://api.medium.com', failure, TOKEN, {}, io.BytesIO(TOKEN.encode()))
    remote = Mock(side_effect=error)
    monkeypatch.setattr(service, 'urlopen', remote)
    with pytest.raises(HTTPException) as result:
        transport('/users/author/posts', TOKEN, payload={'title': 'Example'})
    assert result.value.status_code == code
    assert TOKEN not in str(result.value.detail)
    assert remote.call_count == 1
    request = remote.call_args.args[0]
    assert request.get_header('Authorization') == 'Bearer ' + TOKEN
    assert TOKEN not in request.full_url


def test_medium_publish_cannot_be_forged_through_share_click():
    from engagement_service import validate_share_click
    with pytest.raises(HTTPException):
        validate_share_click({'channel': 'medium_publish', 'event_id': str(uuid.uuid4())})


def test_storage_outage_is_not_reported_as_connection_conflict(monkeypatch):
    from botocore.exceptions import ClientError
    error = ClientError({'Error': {'Code': 'ProvisionedThroughputExceededException'}}, 'TransactWriteItems')
    monkeypatch.setattr(service, 'dynamodb_transact_write', Mock(side_effect=error))
    with pytest.raises(ClientError):
        service._write('example', None, None)


def test_missing_encryption_configuration_disables_connection(setup):
    setup.config.clear()
    assert service.connection_status(setup.actor, setup.actor)['configured'] is False
    with pytest.raises(HTTPException) as result:
        connect(setup)
    assert result.value.status_code == 503
    setup.call.assert_not_called()


def test_history_failure_preserves_remote_success(setup):
    connect(setup)
    setup.history.side_effect = RuntimeError('Storage unavailable')
    result = service.share_article(setup.article, setup.actor, None)
    assert result['shared'] is True
    assert not hasattr(setup.article, 'medium_shared_at')
