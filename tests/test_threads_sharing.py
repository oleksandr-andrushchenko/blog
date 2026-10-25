"""Threads publishing uses owner-bound OAuth and encrypted, API-owned tokens."""
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import notifications
import shared_utils
import article_sharing
import threads_sharing_service as service
from web import HTTPException, Request

TOKEN = 'synthetic-threads-access-token'


@pytest.fixture
def actor():
    return SimpleNamespace(id='threads-test-' + str(uuid.uuid4()), status='active')


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(article_sharing, 'record_article_share', Mock(return_value=True))
    monkeypatch.setattr(service, '_post_permalink', Mock(return_value='https://www.threads.com/@example_creator/post/example'))
    config = {'threads_app_id': 'example-app', 'threads_app_secret': 'synthetic-app-secret',
              'threads_token_encryption_key': Fernet.generate_key().decode(),
              'api_base_url': 'https://api.catalog.example', 'web_base_url': 'https://catalog.example'}
    monkeypatch.setattr(service, 'get_config', lambda: config)
    with monkeypatch.context() as templates:
        templates.setattr(shared_utils, 'get_function_templates_dir',
                          lambda: str(ROOT / 'api-lambda/templates'))
        environment = shared_utils.get_jinja2_env()
    monkeypatch.setattr(service, 'jinja2_env', lambda: environment)
    monkeypatch.setattr(service, 'get_article_url', lambda *a, **k: 'https://catalog.example/@creator/example')
    monkeypatch.setattr(service, 'find_category', lambda slug: SimpleNamespace(name='Photography'))

    def call(path, **params):
        return {
            '/oauth/access_token': {'access_token': 'short-lived-token', 'user_id': '123'},
            '/access_token': {'access_token': TOKEN, 'expires_in': 5184000},
            '/refresh_access_token': {'access_token': TOKEN + '-refreshed', 'expires_in': 5184000},
            '/v1.0/me': {'id': '123', 'username': 'example_creator'},
            '/v1.0/me/threads': {'id': '456'},
        }[path]
    mock = Mock(side_effect=call)
    monkeypatch.setattr(service, 'threads_call', mock)
    return mock, config


@pytest.fixture
def storage(monkeypatch, actor):
    # Real DynamoDB catches conditional-expression, Decimal, and transaction errors
    # that dictionary mocks cannot detect. This is the isolated app_test table.
    from test_utils import dynamodb_table, recreate_dynamodb_table
    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    dynamodb_table.put_item(Item={**key, 'id': actor.id, 'name': 'Example', 'created_at': 1})
    yield dynamodb_table
    for sk in ('META', service.INTEGRATION_KEY, service.PENDING_KEY):
        dynamodb_table.delete_item(Key={**key, 'sk': sk})


def begin(actor):
    result, binding = service.begin_connection(actor, actor)
    state = parse_qs(urlsplit(result['connect_url']).query)['state'][0]
    return dict(state=state, binding=binding, code='single-use-code')


def connect(actor):
    attempt = begin(actor)
    assert service.complete_connection(actor, **attempt)
    return attempt


def article(actor, **changes):
    return SimpleNamespace(**{'id': 'example-article', 'owner_id': actor.id, 'result_files': [],
                             'status': 'published', 'title': 'Example', 'category': 'photography',
                             'preview': '<p>A useful description.</p>', 'is_premium': False,
                             'template': {'content': 'NEVER PUBLISH THE TEMPLATE'}, **changes})


@pytest.mark.parametrize('continuation', [None, 'existing-progress'])
def test_only_article_owner_can_share_even_with_administrator_permissions(actor, storage, provider, continuation):
    actor.permissions = ['root']
    with pytest.raises(HTTPException) as exc:
        service.share_article(article(actor, owner_id='another-owner'), actor, None, continuation=continuation)
    assert exc.value.status_code == 403
    provider[0].assert_not_called()


@pytest.mark.parametrize('permalink,expected', [
    ('https://www.threads.com/@example_creator/post/example', 'https://www.threads.com/@example_creator/post/example'),
    ('https://www.threads.net/@example_creator/post/example', 'https://www.threads.net/@example_creator/post/example'),
    (None, None),
    ('https://unrelated.example/post/example', None),
    ('https://www.threads.com/@example_creator/post/example?access_token=secret', None),
])
def test_post_permalink_uses_official_read_without_republishing(monkeypatch, permalink, expected):
    call = Mock(return_value={'permalink': permalink})
    monkeypatch.setattr(service, 'threads_call', call)
    assert service._post_permalink('456', TOKEN) == expected
    call.assert_called_once_with('/v1.0/456', token=TOKEN, fields='permalink', timeout=service.CONFIRM_TIMEOUT_SECONDS)


def test_permalink_lookup_failure_preserves_confirmed_share_and_safe_logging(monkeypatch, caplog):
    monkeypatch.setattr(service, 'threads_call', Mock(side_effect=HTTPException(502, 'secret-provider-message')))
    assert service._post_permalink('456', TOKEN) is None
    assert 'secret-provider-message' not in caplog.text and TOKEN not in caplog.text


def test_oauth_encrypts_credentials_and_updates_flag_atomically(actor, storage, provider):
    attempt = begin(actor)
    pending = service._read(actor.id, service.PENDING_KEY)
    assert attempt['state'] not in str(pending) and attempt['binding'] not in str(pending)
    assert service.connection_status(actor, actor)['pending']
    service.complete_connection(actor, **attempt)
    status = service.connection_status(actor, actor)
    assert status == {'configured': True, 'connected': True, 'pending': False,
                      'reconnect_required': False, 'username': 'example_creator'}
    record = service._read(actor.id, service.INTEGRATION_KEY)
    assert TOKEN not in str(record) and provider[1]['threads_app_secret'] not in str(record)
    assert TOKEN not in str(status) and 'encrypted_token' not in status
    user = service._read(actor.id, 'META')
    assert shared_utils.user_from_dynamodb(user).has_threads_integration is True
    assert service._read(actor.id, service.PENDING_KEY) is None
    provider[0].reset_mock()
    with pytest.raises(HTTPException):
        service.complete_connection(actor, **attempt)
    provider[0].assert_not_called()
    service.disconnect(actor, actor)
    assert service._read(actor.id, service.INTEGRATION_KEY) is None
    assert service._read(actor.id, 'META')['has_threads_integration'] is False


@pytest.mark.parametrize('field', ['state', 'binding'])
def test_oauth_requires_state_and_separate_browser_binding(actor, storage, provider, field):
    attempt = begin(actor)
    attempt[field] = 'wrong'
    with pytest.raises(HTTPException) as exc:
        service.complete_connection(actor, **attempt)
    assert exc.value.status_code == 409
    provider[0].assert_not_called()


@pytest.mark.parametrize('change', ['replace', 'disconnect', 'expire', 'ban', 'other-user'])
def test_cancelled_and_inaccessible_attempts_cannot_complete(actor, storage, provider, monkeypatch, change):
    attempt = begin(actor)
    completing = actor
    if change == 'replace':
        begin(actor)
    elif change == 'disconnect':
        service.disconnect(actor, actor)
    elif change == 'expire':
        now = service.utc_now()
        monkeypatch.setattr(service, 'utc_now', lambda: now + service.CONNECT_LIFETIME_MS + 1)
    elif change == 'ban':
        actor.status = 'banned'
    else:
        completing = SimpleNamespace(id='other-user', status='active')
    with pytest.raises(HTTPException):
        service.complete_connection(completing, **attempt)
    provider[0].assert_not_called()
    assert service._read(actor.id, service.INTEGRATION_KEY) is None


@pytest.mark.parametrize('action', ['disconnect', 'replace'])
def test_inflight_oauth_cannot_undo_disconnect_or_replacement(actor, storage, provider, action):
    attempt = begin(actor)
    normal = provider[0].side_effect

    def race(path, **params):
        if path == '/v1.0/me':
            service.disconnect(actor, actor) if action == 'disconnect' else begin(actor)
        return normal(path, **params)
    provider[0].side_effect = race
    with pytest.raises(HTTPException) as exc:
        service.complete_connection(actor, **attempt)
    assert exc.value.status_code == 409
    assert service._read(actor.id, service.INTEGRATION_KEY) is None
    assert not service._read(actor.id, 'META').get('has_threads_integration')


def test_denial_preserves_existing_connection_and_consumes_attempt(actor, storage, provider):
    connect(actor)
    attempt = begin(actor)
    provider[0].reset_mock()
    assert service.complete_connection(actor, **attempt, denied=True) is False
    provider[0].assert_not_called()
    assert service.connection_status(actor, actor)['connected']
    assert service._read(actor.id, service.PENDING_KEY) is None


@pytest.mark.parametrize('method', ['connection_status', 'begin_connection', 'disconnect'])
def test_administrators_cannot_manage_another_users_connection(actor, provider, method):
    other = SimpleNamespace(id='administrator', status='active', permissions=['*'])
    with pytest.raises(HTTPException) as exc:
        getattr(service, method)(actor, other)
    assert exc.value.status_code == 403
    provider[0].assert_not_called()


def test_share_contains_metadata_and_canonical_link_without_template(actor, storage, provider):
    connect(actor)
    provider[0].reset_mock()
    assert service.share_article(article(actor, preview='<p>' + '🌻' * 1000 + '</p>'), actor, None)['post_id'] == '456'
    call = provider[0].call_args
    assert call.args == ('/v1.0/me/threads',)
    assert call.kwargs['auto_publish_text'] == 'true'
    assert call.kwargs['media_type'] == 'TEXT'
    assert call.kwargs['link_attachment'] == 'https://catalog.example/@creator/example'
    text = call.kwargs['text']
    assert len(text) <= 500
    assert text.startswith('Example\nPhotography\n\n🌻')
    assert 'Read the article: https://catalog.example/@creator/example' in text
    assert text.endswith('Follow this account 📣\nhttps://www.threads.com/@example_creator')
    assert 'NEVER PUBLISH' not in text and '<p>' not in text
    service.share_article(article(actor, is_premium=True, preview=''), actor, None)
    text = provider[0].call_args.kwargs['text']
    assert 'Read the article:' in text and 'NEVER PUBLISH' not in text


def test_slow_publish_is_confirmed_by_readback_without_reposting(actor, storage, provider, monkeypatch):
    connect(actor)
    now = service.utc_now()
    monkeypatch.setattr(service, 'utc_now', lambda: now)
    payload = {}

    def call(path, **params):
        assert path == '/v1.0/me/threads'
        if params.get('method') == 'POST':
            payload.update(params)
            raise service.ThreadsRequestUnconfirmed(502, 'Confirmation timed out')
        assert params['timeout'] == service.CONFIRM_TIMEOUT_SECONDS
        assert params['limit'] == 5
        return {'data': [{'id': 'confirmed-post', 'text': payload['text'],
                          'timestamp': datetime.fromtimestamp(now / 1000, timezone.utc).isoformat()}]}
    provider[0].side_effect = call
    provider[0].reset_mock()
    result = service.share_article(article(actor), actor, None)
    assert result == {'shared': True, 'post_id': 'confirmed-post', 'username': 'example_creator'}
    article_sharing.record_article_share.assert_called_once()
    assert payload['timeout'] == service.PUBLISH_TIMEOUT_SECONDS
    assert len(provider[0].call_args_list) == 2
    assert sum(call.kwargs.get('method') == 'POST' for call in provider[0].call_args_list) == 1


@pytest.mark.parametrize('case', ['old', 'future', 'different-text', 'bad-timestamp', 'missing-timestamp',
                                  'naive-timestamp', 'unavailable', 'not-visible', 'malformed-list', 'missing-id'])
def test_unconfirmed_publish_never_reports_failure_or_reposts(actor, storage, provider, monkeypatch, case):
    connect(actor)
    now = service.utc_now()
    monkeypatch.setattr(service, 'utc_now', lambda: now)
    payload = {}

    def call(path, **params):
        if params.get('method') == 'POST':
            payload.update(params)
            if case == 'missing-id':
                return {}
            raise service.ThreadsRequestUnconfirmed(502, 'Confirmation timed out')
        if case == 'unavailable':
            raise HTTPException(502, 'Read unavailable')
        if case == 'not-visible':
            return {'data': []}
        if case == 'malformed-list':
            return {'data': {}}
        stamp = now - 2000 if case == 'old' else now + 10000 if case == 'future' else now
        post = {'id': 'older-or-unrelated-post', 'text': payload['text'],
                'timestamp': datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()}
        if case == 'different-text':
            post['text'] = 'Another post'
        if case == 'bad-timestamp':
            post['timestamp'] = 'invalid'
        if case == 'missing-timestamp':
            post.pop('timestamp')
        if case == 'naive-timestamp':
            post['timestamp'] = '2026-10-22T06:00:00'
        if case == 'missing-id':
            post.pop('id')
        return {'data': [None, post]}
    provider[0].side_effect = call
    provider[0].reset_mock()
    assert service.share_article(article(actor), actor, None) == {
        'shared': False, 'status': 'unconfirmed', 'username': 'example_creator',
    }
    article_sharing.record_article_share.assert_not_called()
    assert len(provider[0].call_args_list) == 2
    assert sum(call.kwargs.get('method') == 'POST' for call in provider[0].call_args_list) == 1


def test_rejected_publish_does_not_trigger_confirmation_read(actor, storage, provider):
    connect(actor)
    provider[0].reset_mock()
    provider[0].side_effect = HTTPException(422, 'Threads rejected the post')
    with pytest.raises(HTTPException) as exc:
        service.share_article(article(actor), actor, None)
    assert exc.value.status_code == 422
    assert provider[0].call_count == 1


@pytest.mark.parametrize('case', ['unpublished', 'rejected', 'banned', 'missing', 'expired', 'tampered', 'changed-app'])
def test_share_requires_live_authorized_connection(actor, storage, provider, case):
    connect(actor)
    value = article(actor)
    if case in ('unpublished', 'rejected'):
        value.status = case
    elif case == 'banned':
        actor.status = 'banned'
    elif case == 'missing':
        service.disconnect(actor, actor)
    elif case == 'changed-app':
        provider[1]['threads_app_id'] = 'another-app'
    else:
        record = service._read(actor.id, service.INTEGRATION_KEY)
        record['expires_at' if case == 'expired' else 'encrypted_token'] = 1 if case == 'expired' else 'invalid-token'
        storage.put_item(Item=record)
    provider[0].reset_mock()
    with pytest.raises(HTTPException):
        service.share_article(value, actor, None)
    provider[0].assert_not_called()


def test_refresh_keeps_tokens_encrypted_and_does_not_resurrect_disconnected_account(actor, storage, provider, monkeypatch):
    connect(actor)
    now = service.utc_now()
    monkeypatch.setattr(service, 'utc_now', lambda: now + service.DAY_MS + 1)
    service.share_article(article(actor), actor, None)
    assert provider[0].call_args.kwargs['token'] == TOKEN + '-refreshed'
    assert TOKEN not in str(service._read(actor.id, service.INTEGRATION_KEY))
    later = now + 2 * service.DAY_MS + 2
    monkeypatch.setattr(service, 'utc_now', lambda: later)
    normal = provider[0].side_effect

    def race(path, **params):
        if path == '/refresh_access_token':
            service.disconnect(actor, actor)
        return normal(path, **params)
    provider[0].side_effect = race
    provider[0].reset_mock()
    with pytest.raises(HTTPException) as exc:
        service.share_article(article(actor), actor, None)
    assert exc.value.status_code == 409
    assert all(call.args[0] != '/v1.0/me/threads' for call in provider[0].call_args_list)
    assert service._read(actor.id, service.INTEGRATION_KEY) is None


def test_encrypted_tokens_cannot_be_moved_between_application_accounts(actor, storage, provider):
    connect(actor)
    other = SimpleNamespace(id='other-threads-user', status='active')
    record = service._read(actor.id, service.INTEGRATION_KEY)
    key = {'pk': 'USER#' + other.id, 'sk': service.INTEGRATION_KEY}
    storage.put_item(Item={**record, **key})
    try:
        with pytest.raises(HTTPException) as exc:
            service.share_article(article(other), other, None)
        assert exc.value.status_code == 409
    finally:
        storage.delete_item(Key=key)


threads_transport = service.threads_call


@pytest.mark.parametrize('error,status', [
    (HTTPError('secret-url', 401, 'secret-token', {}, io.BytesIO()), 409),
    (HTTPError('secret-url', 429, 'secret-token', {}, io.BytesIO()), 429),
    (HTTPError('secret-url', 500, 'secret-token', {}, io.BytesIO()), 502),
    (URLError('secret-token'), 502),
])
def test_provider_failures_are_redacted_and_not_retried(monkeypatch, error, status, caplog):
    transport = Mock(side_effect=error)
    monkeypatch.setattr(service, 'urlopen', transport)
    with pytest.raises(HTTPException) as exc:
        threads_transport('/v1.0/me/threads', method='POST', token=TOKEN, text='Example', auto_publish_text='true')
    assert exc.value.status_code == status
    assert 'secret' not in exc.value.detail and TOKEN not in exc.value.detail
    assert transport.call_count == 1
    assert 'secret' not in caplog.text and TOKEN not in caplog.text


def test_provider_error_logs_only_operation_and_numeric_codes(monkeypatch, caplog):
    body = {'error': {'code': 190, 'error_subcode': 463, 'message': 'secret-code ' + TOKEN,
                      'fbtrace_id': 'secret-trace'}}
    error = HTTPError('https://graph.threads.com/access_token?client_secret=secret-value', 400,
                      'secret-code', {}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(service, 'urlopen', Mock(side_effect=error))
    with pytest.raises(HTTPException):
        threads_transport('/access_token', token=TOKEN, client_secret='secret-value', grant_type='th_exchange_token')
    assert 'operation=/access_token' in caplog.text
    assert 'provider_code=190 provider_subcode=463' in caplog.text
    assert 'secret' not in caplog.text and TOKEN not in caplog.text


@pytest.mark.parametrize('path,step', [
    ('/oauth/access_token', 'authorization'), ('/access_token', 'token'), ('/v1.0/me', 'profile'),
])
def test_connection_errors_identify_failing_step_without_saving_credentials(actor, storage, provider, path, step):
    attempt = begin(actor)
    normal = provider[0].side_effect

    def fail(resource, **params):
        if resource == path:
            raise HTTPException(422, 'Threads rejected the request')
        return normal(resource, **params)
    provider[0].side_effect = fail
    with pytest.raises(service.ThreadsConnectionError) as exc:
        service.complete_connection(actor, **attempt)
    assert exc.value.connection_step == step
    assert service._read(actor.id, service.INTEGRATION_KEY) is None
    assert not service._read(actor.id, 'META').get('has_threads_integration')


def test_transport_puts_token_in_header_and_publishing_fields_in_body(monkeypatch):
    transport = Mock(return_value=io.BytesIO(b'{"id":"456"}'))
    monkeypatch.setattr(service, 'urlopen', transport)
    threads_transport('/v1.0/me/threads', method='POST', token=TOKEN, timeout=15,
                      text='Example & more', auto_publish_text='true')
    request = transport.call_args.args[0]
    assert TOKEN not in request.full_url and request.get_header('Authorization') == 'Bearer ' + TOKEN
    assert parse_qs(request.data.decode())['text'] == ['Example & more']
    assert 'timeout' not in parse_qs(request.data.decode())
    assert transport.call_args.kwargs['timeout'] == 15


def test_token_exchange_and_refresh_send_explicit_access_token(actor, storage, provider, monkeypatch):
    """Token endpoints follow Meta's explicit access_token parameter contract."""
    requests = []
    responses = {
        '/oauth/access_token': {'access_token': 'short-lived-token', 'user_id': '123'},
        '/access_token': {'access_token': TOKEN, 'expires_in': 5184000},
        '/refresh_access_token': {'access_token': TOKEN + '-refreshed', 'expires_in': 5184000},
        '/v1.0/me': {'id': '123', 'username': 'example_creator'},
        '/v1.0/me/threads': {'id': '456'},
    }

    def transport(request, timeout):
        path = urlsplit(request.full_url).path
        requests.append(request)
        if path in ('/access_token', '/refresh_access_token'):
            expected = 'short-lived-token' if path == '/access_token' else TOKEN
            assert parse_qs(urlsplit(request.full_url).query)['access_token'] == [expected]
            assert request.get_header('Authorization') is None
        return io.BytesIO(json.dumps(responses[path]).encode())
    monkeypatch.setattr(service, 'threads_call', threads_transport)
    monkeypatch.setattr(service, 'urlopen', transport)
    connect(actor)
    now = service.utc_now()
    monkeypatch.setattr(service, 'utc_now', lambda: now + service.DAY_MS + 1)
    service.share_article(article(actor), actor, None)
    assert [urlsplit(request.full_url).path for request in requests] == [
        '/oauth/access_token', '/access_token', '/v1.0/me', '/refresh_access_token', '/v1.0/me/threads',
    ]
    assert requests[-1].get_header('Authorization') == 'Bearer ' + TOKEN + '-refreshed'


def test_threads_oauth_codes_and_state_do_not_appear_in_access_logs():
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'https',
                       'server': ('api.catalog.example', 443), 'path': '/integrations/threads/callback',
                       'query_string': b'code=secret-code&state=secret-state',
                       'headers': [(b'referer', b'https://api.catalog.example/integrations/threads/callback?code=secret-referrer')]})
    text = notifications.get_access_log(request, 303)[1]
    assert '/integrations/threads/callback' in text
    assert 'secret' not in text


def test_threads_flag_defaults_to_false_for_legacy_users():
    user = {'id': 'legacy', 'name': 'Example', 'created_at': 1}
    assert shared_utils.user_from_dynamodb(user).has_threads_integration is False


def test_missing_configuration_still_allows_removing_saved_credentials(actor, storage, provider):
    connect(actor)
    provider[1]['threads_token_encryption_key'] = ''
    status = service.connection_status(actor, actor)
    assert not status['configured'] and status['reconnect_required']
    service.disconnect(actor, actor)
    assert service._read(actor.id, service.INTEGRATION_KEY) is None


def test_provider_failure_consumes_code_without_replacing_existing_connection(actor, storage, provider):
    connect(actor)
    existing = service._read(actor.id, service.INTEGRATION_KEY)
    attempt = begin(actor)
    provider[0].side_effect = HTTPException(502, 'Threads unavailable')
    with pytest.raises(HTTPException):
        service.complete_connection(actor, **attempt)
    provider[0].reset_mock()
    with pytest.raises(HTTPException):
        service.complete_connection(actor, **attempt)
    provider[0].assert_not_called()
    assert service._read(actor.id, service.INTEGRATION_KEY) == existing


def test_threads_callback_uses_secure_cookie_and_returns_to_owner_profile(actor, provider, monkeypatch, caplog):
    """Unit-check response construction; functional requests use the separate API test service."""
    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location('threads_response_tests', ROOT / 'api-lambda/app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(service, 'begin_connection', lambda *args: (
        {'connect_url': 'https://www.threads.com/oauth/authorize?state=example', 'expires_at': 1000}, 'binding-cookie'))
    request = Request({'type': 'http', 'method': 'POST', 'path': '/users/example/integrations/threads',
                       'headers': [(b'content-type', b'application/json')]})
    response = asyncio.run(module.connect_threads(actor, actor, request))
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
    assert 'Max-Age=600' in cookie and 'Path=/integrations/threads/callback' in cookie
    assert 'binding-cookie' not in response.body.decode()
    complete = Mock(return_value=True)
    monkeypatch.setattr(service, 'complete_connection', complete)
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'https', 'app': module.app,
                       'server': ('api.catalog.example', 443), 'path': '/integrations/threads/callback',
                       'query_string': b'code=secret-code&state=example',
                       'headers': [(b'cookie', b'threads_connection=binding-cookie')]})
    response = asyncio.run(module.threads_callback(actor, request))
    assert response.status_code == 303 and response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Location'].endswith(f'/users/{actor.id}/edit?threads=connected#integrations')
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert complete.call_args.kwargs == {'state': 'example', 'binding': 'binding-cookie',
                                         'code': 'secret-code', 'denied': False}
    complete.side_effect = service.ThreadsConnectionError(HTTPException(422, 'secret-provider-message'), 'token')
    response = asyncio.run(module.threads_callback(actor, request))
    assert response.headers['Location'].endswith('?threads=error&threads_reason=token#integrations')
    assert 'step=token status=422' in caplog.text
    assert 'secret' not in caplog.text
    assert 'secret' not in response.headers['Location']


def test_unconfirmed_share_response_is_accepted_without_claiming_success(actor, monkeypatch):
    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location('threads_share_response_tests', ROOT / 'api-lambda/app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(service, 'share_article', lambda *args: {
        'shared': False, 'status': 'unconfirmed', 'username': 'example_creator',
    })
    request = Request({'type': 'http', 'method': 'POST', 'path': '/articles/example/share/threads',
                       'headers': [(b'content-type', b'application/json')]})
    response = asyncio.run(module.share_article_threads(article(actor), actor, request))
    assert response.status_code == 202
    assert response.headers['Cache-Control'] == 'no-store'
    assert json.loads(response.body) == {'shared': False, 'status': 'unconfirmed', 'username': 'example_creator'}



def test_article_cover_finishes_without_repeating_publish(actor, storage, provider, monkeypatch):
    connect(actor)
    monkeypatch.setattr(article_sharing, 'get_static_url', lambda *a, **k: 'https://static.example/cover.jpg')
    now = [service.utc_now()]
    monkeypatch.setattr(service, 'utc_now', lambda: now[0])
    calls = provider[0]
    calls.reset_mock()
    published = []
    def call(path, **params):
        if path == '/v1.0/me/threads':
            assert params['media_type'] == 'IMAGE'
            assert params['image_url'] == 'https://static.example/cover.jpg'
            return {'id': '100'}
        if path == '/v1.0/100':
            return {'status': 'PUBLISHED' if published else 'FINISHED'}
        if path == '/v1.0/me/threads_publish':
            published.append(params)
            return {'id': '200'}
        pytest.fail('Unexpected provider request: ' + path)
    calls.side_effect = call
    value = article(actor, image_filename='cover.jpg')
    response = service.share_article(value, actor, None)
    assert response['status'] == 'processing'
    assert TOKEN not in str(response)
    now[0] += 30000
    result = service.share_article(value, actor, None, continuation=response['continuation'])
    assert result['shared'] is True and result['post_id'] == '200'
    assert len(published) == 1
    replay = service.share_article(value, actor, None, continuation=response['continuation'])
    assert replay['status'] == 'unconfirmed'
    assert len(published) == 1


def test_malformed_referrer_cannot_break_access_logging():
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'https', 'server': ('site.example', 443),
                       'path': '/', 'query_string': b'', 'headers': [(b'referer', b'http://[?code=secret-code')]})
    _, message = notifications.get_access_log(request, 200)
    assert 'secret-code' not in message
