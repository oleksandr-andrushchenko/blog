"""LinkedIn connections bind consent to the owner and publishing requires confirmation."""
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography.fernet import Fernet
import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import article_sharing
import linkedin_service as service
import notifications
import shared_utils
from web import HTTPException, Request

TOKEN = 'synthetic-linkedin-token'
AUTH_COOKIE = 'synthetic-session-cookie'
POST_ID = 'urn:li:share:123456'
transport = service.linkedin_call


@pytest.fixture
def setup(monkeypatch):
    from test_utils import dynamodb_table, recreate_dynamodb_table

    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    actor = SimpleNamespace(id='linkedin-' + uuid.uuid4().hex, status='active')
    key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    dynamodb_table.put_item(Item={**key, 'id': actor.id, 'name': 'Creator', 'created_at': 1})
    config = {'linkedin_app_id': 'client-id', 'linkedin_app_secret': 'client-secret',
              'linkedin_token_encryption_key': Fernet.generate_key().decode(), 'linkedin_api_version': '202610',
              'api_base_url': 'https://api.example.com', 'web_base_url': 'https://example.com'}
    monkeypatch.setattr(service, 'get_config', lambda: config)
    responses = {
        '/oauth/v2/accessToken': {'access_token': TOKEN, 'expires_in': 3600,
                                'scope': 'openid profile w_member_social'},
        '/v2/userinfo': {'sub': 'member_123', 'name': 'Example Creator'},
        '/rest/posts': {'id': POST_ID},
    }
    call = Mock(side_effect=lambda path, **kwargs: responses[path])
    monkeypatch.setattr(service, 'linkedin_call', call)
    history = Mock(return_value=True)
    monkeypatch.setattr(article_sharing, 'record_article_share', history)
    monkeypatch.setattr(service, 'get_article_url', lambda *a, **k: 'https://example.com/@creator/article')
    monkeypatch.setattr(service, 'find_category', lambda category: SimpleNamespace(name='System Design'))
    request = SimpleNamespace(cookies={'token': AUTH_COOKIE})
    article = SimpleNamespace(id='article', owner_id=actor.id, status='published', title='An example',
                              category='system-design', preview='<p>Public preview</p>',
                              content='PRIVATE ARTICLE BODY', is_premium=True, linkedin_shared_at=None)
    yield SimpleNamespace(actor=actor, config=config, responses=responses, call=call, history=history,
                          request=request, article=article, table=dynamodb_table, key=key)
    for sk in ('META', service.INTEGRATION_KEY, service.PENDING_KEY):
        dynamodb_table.delete_item(Key={**key, 'sk': sk})


def begin(setup):
    result, binding = service.begin_connection(setup.actor, setup.actor, setup.request)
    query = parse_qs(urlsplit(result['connect_url']).query)
    assert query['scope'] == ['openid profile w_member_social']
    assert query['redirect_uri'] == ['https://api.example.com/integrations/linkedin/callback']
    return {'state': query['state'][0], 'binding': binding, 'code': 'single-use-code', 'auth_cookie': AUTH_COOKIE}


def connect(setup):
    attempt = begin(setup)
    assert service.complete_connection(setup.actor, **attempt)
    return attempt


def test_consent_is_encrypted_owner_bound_and_single_use(setup):
    attempt = begin(setup)
    pending = service._read(setup.actor.id, service.PENDING_KEY)
    assert all(value not in str(pending) for value in (attempt['state'], attempt['binding'], AUTH_COOKIE))
    service.complete_connection(setup.actor, **attempt)
    record = service._read(setup.actor.id, service.INTEGRATION_KEY)
    assert TOKEN not in str(record) and 'client-secret' not in str(record)
    assert service.connection_status(setup.actor, setup.actor) == {
        'configured': True, 'connected': True, 'pending': False,
        'reconnect_required': False, 'username': 'Example Creator'}
    assert setup.table.get_item(Key=setup.key)['Item']['has_linkedin_integration'] is True
    before = setup.call.call_count
    with pytest.raises(HTTPException) as error:
        service.complete_connection(setup.actor, **attempt)
    assert error.value.status_code == 409
    assert setup.call.call_count == before


@pytest.mark.parametrize('field', ['state', 'binding', 'auth_cookie'])
def test_wrong_state_browser_or_session_never_exchanges_codes(setup, field):
    attempt = begin(setup)
    attempt[field] = 'different-browser-or-session'
    with pytest.raises(HTTPException) as error:
        service.complete_connection(setup.actor, **attempt)
    assert error.value.status_code == 409
    setup.call.assert_not_called()


@pytest.mark.parametrize('change', ['expired', 'replaced', 'different-owner', 'banned'])
def test_invalid_connection_attempts_do_not_exchange_codes(setup, change):
    attempt = begin(setup)
    actor = setup.actor
    if change == 'expired':
        setup.table.update_item(Key={**setup.key, 'sk': service.PENDING_KEY},
                                UpdateExpression='SET expires_at = :zero', ExpressionAttributeValues={':zero': 0})
    elif change == 'replaced':
        begin(setup)
    elif change == 'different-owner':
        actor = SimpleNamespace(id='someone-else', status='active')
    else:
        actor.status = 'banned'
    with pytest.raises(HTTPException):
        service.complete_connection(actor, **attempt)
    setup.call.assert_not_called()


def test_denied_consent_consumes_attempt_without_provider_calls(setup):
    attempt = begin(setup)
    assert service.complete_connection(setup.actor, **attempt, denied=True) is False
    assert service._read(setup.actor.id, service.PENDING_KEY) is None
    setup.call.assert_not_called()


def test_failed_exchange_is_never_repeated(setup):
    attempt = begin(setup)
    setup.call.side_effect = HTTPException(502, 'Provider unavailable')
    with pytest.raises(HTTPException):
        service.complete_connection(setup.actor, **attempt)
    with pytest.raises(HTTPException) as error:
        service.complete_connection(setup.actor, **attempt)
    assert error.value.status_code == 409
    assert setup.call.call_count == 1


@pytest.mark.parametrize('change', ['disconnect', 'replace', 'ban'])
def test_in_flight_callback_cannot_override_disconnect_replacement_or_ban(setup, change):
    attempt = begin(setup)
    original = setup.call.side_effect

    def interrupted(path, **kwargs):
        if path == '/v2/userinfo':
            if change == 'disconnect':
                service.disconnect(setup.actor, setup.actor)
            elif change == 'replace':
                begin(setup)
            else:
                setup.table.update_item(Key=setup.key, UpdateExpression='SET #status = :banned',
                                        ExpressionAttributeNames={'#status': 'status'},
                                        ExpressionAttributeValues={':banned': 'banned'})
        return original(path, **kwargs)

    setup.call.side_effect = interrupted
    with pytest.raises(HTTPException) as error:
        service.complete_connection(setup.actor, **attempt)
    assert error.value.status_code == 409
    assert not service._read(setup.actor.id, service.INTEGRATION_KEY)


@pytest.mark.parametrize('change', ['app', 'key', 'expired', 'identity'])
def test_expired_or_rebound_credentials_require_reconnection(setup, change):
    connect(setup)
    if change == 'app':
        setup.config['linkedin_app_id'] = 'another-app'
    elif change == 'key':
        setup.config['linkedin_token_encryption_key'] = Fernet.generate_key().decode()
    else:
        field, value = ('expires_at', 0) if change == 'expired' else ('linkedin_user_id', 'other-member')
        setup.table.update_item(Key={**setup.key, 'sk': service.INTEGRATION_KEY},
                                UpdateExpression=f'SET {field} = :value', ExpressionAttributeValues={':value': value})
    assert service.connection_status(setup.actor, setup.actor)['reconnect_required'] is True
    with pytest.raises(HTTPException) as error:
        service.share_article(setup.article, setup.actor, None)
    assert error.value.status_code == 409


def test_disconnect_removes_local_credentials_even_without_configuration(setup):
    connect(setup)
    setup.config['linkedin_app_secret'] = ''
    assert service.connection_status(setup.actor, setup.actor)['configured'] is False
    service.disconnect(setup.actor, setup.actor)
    assert service._read(setup.actor.id, service.INTEGRATION_KEY) is None
    assert setup.table.get_item(Key=setup.key)['Item']['has_linkedin_integration'] is False


def test_publish_exports_only_preview_and_canonical_link_and_records_confirmed_success(setup):
    connect(setup)
    result = service.share_article(setup.article, setup.actor, None)
    payload = setup.call.call_args.kwargs['payload']
    assert payload['author'] == 'urn:li:person:member_123'
    assert payload['content']['article']['source'] == 'https://example.com/@creator/article'
    assert payload['content']['article']['description'] == 'Public preview'
    assert 'Read the full article on SysDesPro:' in payload['commentary']
    assert 'PRIVATE ARTICLE BODY' not in json.dumps(payload)
    assert result['post_url'] == 'https://www.linkedin.com/feed/update/' + POST_ID + '/'
    assert setup.article.linkedin_shared_at is not None
    assert setup.history.call_args.args[2] == 'linkedin_publish'


def test_post_text_is_bounded_and_escapes_little_text_markup(setup):
    connect(setup)
    setup.article.preview = '<p>' + '@[mention](person) 😀' * 500 + '</p>'
    service.share_article(setup.article, setup.actor, None)
    payload = setup.call.call_args.kwargs['payload']
    assert len(payload['commentary'].encode('utf-16-le')) // 2 <= 3000
    assert '\\@\\[mention\\]' in payload['commentary']
    assert payload['content']['article']['source'].endswith('/@creator/article')


@pytest.mark.parametrize('change', ['other-owner', 'banned', 'unpublished'])
def test_only_active_owners_can_publish_their_published_articles(setup, change):
    if change == 'other-owner':
        setup.article.owner_id = 'someone-else'
    elif change == 'banned':
        setup.actor.status = 'banned'
    else:
        setup.article.status = 'unpublished'
    with pytest.raises(HTTPException):
        service.share_article(setup.article, setup.actor, None)
    setup.call.assert_not_called()


@pytest.mark.parametrize('result', [{}, {'id': ''}, {'id': 'https://evil.example'}, 'timeout'])
def test_uncertain_publishing_does_not_record_or_retry(setup, result):
    connect(setup)
    setup.call.reset_mock()
    if result == 'timeout':
        setup.call.side_effect = service.LinkedInRequestUnconfirmed(502, 'Unconfirmed')
    else:
        setup.responses['/rest/posts'] = result
    assert service.share_article(setup.article, setup.actor, None) == {'shared': False, 'status': 'unconfirmed'}
    assert setup.call.call_count == 1
    setup.history.assert_not_called()
    assert setup.article.linkedin_shared_at is None


def test_remote_success_survives_history_failure(setup):
    connect(setup)
    setup.history.side_effect = RuntimeError('Database unavailable')
    assert service.share_article(setup.article, setup.actor, None)['shared'] is True


def test_confirmed_publish_cannot_be_forged_as_a_share_click():
    from engagement_service import validate_share_click

    with pytest.raises(HTTPException) as error:
        validate_share_click({'channel': 'linkedin_publish', 'event_id': str(uuid.uuid4())})
    assert error.value.status_code == 422
    assert validate_share_click({'channel': 'linkedin_share_click', 'event_id': str(uuid.uuid4())})[0] == 'linkedin_share_click'


def test_callback_uses_secure_cookie_returns_to_owner_and_hides_errors(setup, monkeypatch, caplog):
    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location('linkedin_response_tests', ROOT / 'api-lambda/app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(service, 'begin_connection', lambda *args: (
        {'connect_url': 'https://www.linkedin.com/oauth/v2/authorization?state=example'}, 'browser-binding'))
    request = Request({'type': 'http', 'method': 'POST', 'path': '/users/example/integrations/linkedin',
                       'headers': [(b'content-type', b'application/json')]})
    response = asyncio.run(module.connect_linkedin(setup.actor, setup.actor, request))
    cookie = response.headers['set-cookie']
    assert all(value in cookie for value in ('HttpOnly', 'Secure', 'SameSite=lax', 'Max-Age=600',
                                             'Path=/integrations/linkedin/callback'))
    assert 'browser-binding' not in response.body.decode()
    complete = Mock(return_value=True)
    monkeypatch.setattr(service, 'complete_connection', complete)
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'https', 'app': module.app,
                       'server': ('api.example.com', 443), 'path': '/integrations/linkedin/callback',
                       'query_string': b'code=secret-code&state=example',
                       'headers': [(b'cookie', b'linkedin_connection=browser-binding; token=session-token')]})
    response = asyncio.run(module.linkedin_callback(setup.actor, request))
    assert response.status_code == 303
    assert response.headers['Location'].endswith(f'/users/{setup.actor.id}/edit?linkedin=connected#integrations')
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert complete.call_args.kwargs == {'state': 'example', 'binding': 'browser-binding',
                                         'code': 'secret-code', 'auth_cookie': 'session-token', 'denied': False}
    complete.side_effect = HTTPException(502, 'secret-provider-error')
    response = asyncio.run(module.linkedin_callback(setup.actor, request))
    assert 'linkedin=error' in response.headers['Location']
    assert 'secret' not in response.headers['Location'] + caplog.text


@pytest.mark.parametrize('status, expected', [(400, 422), (401, 409), (403, 409), (429, 429), (500, 502)])
def test_transport_does_not_leak_secrets_or_retry(setup, monkeypatch, status, expected, caplog):
    opener = Mock()
    opener.open.side_effect = HTTPError('https://api.linkedin.com/rest/posts', status, TOKEN, {}, io.BytesIO(TOKEN.encode()))
    monkeypatch.setattr(service, 'build_opener', Mock(return_value=opener))
    with pytest.raises(HTTPException) as error:
        transport('/rest/posts', token=TOKEN, payload={'test': True})
    assert error.value.status_code == expected
    assert TOKEN not in str(error.value.detail) + caplog.text
    assert opener.open.call_count == 1


def test_transport_uses_current_api_and_requires_201_with_post_id(setup, monkeypatch):
    response = Mock(status=201, headers={'x-restli-id': POST_ID})
    opener = Mock()
    opener.open.return_value.__enter__ = Mock(return_value=response)
    opener.open.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(service, 'build_opener', Mock(return_value=opener))
    assert transport('/rest/posts', token=TOKEN, payload={'title': 'Example'}) == {'id': POST_ID}
    request = opener.open.call_args.args[0]
    assert request.full_url == 'https://api.linkedin.com/rest/posts'
    assert request.get_header('Linkedin-version') == '202610'
    assert request.get_header('Authorization') == 'Bearer ' + TOKEN
    response.status = 200
    with pytest.raises(service.LinkedInRequestUnconfirmed):
        transport('/rest/posts', token=TOKEN, payload={})


def test_callback_queries_and_referrers_are_redacted():
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'https',
                       'server': ('api.example.com', 443), 'path': '/integrations/linkedin/callback',
                       'query_string': b'code=secret-code&state=secret-state',
                       'headers': [(b'referer', b'https://api.example.com/integrations/linkedin/callback?code=secret-referrer')]})
    _, text = notifications.get_access_log(request, 303)
    assert '/integrations/linkedin/callback' in text
    assert 'secret-' not in text
