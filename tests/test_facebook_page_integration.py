"""Facebook Page OAuth and publishing use guarded records and a mocked Graph API."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
import uuid

from boto3.dynamodb.conditions import Key
from cryptography.fernet import Fernet
import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT / folder) for folder in ('shared', 'api-lambda')]

import facebook_service as service
import shared_utils
from engagement_service import validate_share_click
from notifications import _access_log_url
from web import HTTPException

AUTH = 'test-session'
PAGE_ID = '123'
POST_ID = '123_456'


@pytest.fixture
def actor():
    return SimpleNamespace(id='facebook-' + str(uuid.uuid4()), status='active', permissions=['root'])


@pytest.fixture
def article(actor):
    return SimpleNamespace(id='article-' + actor.id, owner_id=actor.id, status='published',
                           title='Example article', preview='<p>Public preview</p>', category='system-design',
                           tags=['System Design', 'caching', '2026'], image_filename=None, facebook_shared_at=None)


@pytest.fixture
def storage(monkeypatch, actor, article):
    from test_utils import dynamodb_table, recreate_dynamodb_table

    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    user_key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    article_key = {'pk': 'POST#' + article.id, 'sk': 'META'}
    dynamodb_table.put_item(Item={**user_key, 'status': 'active', 'id': actor.id})
    dynamodb_table.put_item(Item={**article_key, 'id': article.id, 'user_id': actor.id, 'status': 'published'})
    yield dynamodb_table
    for partition in (user_key['pk'], 'POST_SHARE#' + article.id):
        for item in dynamodb_table.query(KeyConditionExpression=Key('pk').eq(partition))['Items']:
            dynamodb_table.delete_item(Key={'pk': item['pk'], 'sk': item['sk']})
    dynamodb_table.delete_item(Key=article_key)
    dynamodb_table.delete_item(Key={'pk': service.SITE_KEY[0], 'sk': service.SITE_KEY[1]})


@pytest.fixture
def provider(monkeypatch, storage):
    monkeypatch.setattr(service, 'config', {
        'facebook_page_app_id': 'synthetic-app', 'facebook_page_app_secret': 'synthetic-secret',
        'facebook_page_token_encryption_key': Fernet.generate_key().decode(),
        'facebook_page_api_version': 'v27.0',
        'api_base_url': 'https://api.example.test', 'web_base_url': 'https://example.test',
    })
    monkeypatch.setattr(service, 'get_article_url', lambda *args, **kwargs: 'https://example.test/articles/example')

    def graph(path, **args):
        if path == '/oauth/access_token':
            return {'access_token': 'private-user-token'}
        if path == '/me/permissions':
            return {'data': [{'permission': scope, 'status': 'granted'} for scope in service.SCOPES]}
        if path == '/me/accounts':
            if args.get('after'):
                return {'data': [{'id': PAGE_ID, 'name': 'Example Page', 'tasks': ['CREATE_CONTENT'],
                                  'access_token': 'private-page-token'}]}
            return {'data': [{'id': '99', 'name': 'Read only Page', 'tasks': ['ANALYZE'],
                              'access_token': 'read-only-token'}],
                    'paging': {'next': 'https://untrusted.example', 'cursors': {'after': 'next'}}}
        if path == '/' + PAGE_ID:
            return {'id': PAGE_ID}
        if path.endswith('/photos'):
            return {'id': '789'}
        if path.endswith('/feed'):
            return {'id': POST_ID}
        raise AssertionError(path)

    mock = Mock(side_effect=graph)
    monkeypatch.setattr(service, 'facebook_call', mock)
    return mock


def begin(actor):
    result, binding = service.begin_connection(actor, AUTH)
    query = parse_qs(urlsplit(result['connect_url']).query)
    assert set(query['scope'][0].split(',')) == service.SCOPES
    return {'state': query['state'][0], 'binding': binding, 'auth_cookie': AUTH, 'code': 'private-code'}


def connect(actor):
    attempt = begin(actor)
    assert service.complete_connection(actor, **attempt)
    assert not service.connection_status(actor, 'other-session')['can_select']
    assert service.list_pages(actor, AUTH) == {'pages': [{'id': PAGE_ID, 'name': 'Example Page'}]}
    assert service.select_page(actor, PAGE_ID, AUTH)['connected']
    return attempt


@pytest.mark.parametrize('field', ['state', 'binding', 'auth_cookie'])
def test_oauth_checks_browser_state_and_signed_in_session(actor, provider, field):
    attempt = begin(actor)
    attempt[field] = 'wrong'
    with pytest.raises(HTTPException) as error:
        service.complete_connection(actor, **attempt)
    assert error.value.status_code == 409
    provider.assert_not_called()


def test_page_selection_consumes_state_and_stores_only_encrypted_credentials(actor, provider):
    attempt = connect(actor)
    pointer, connection = service._connection()
    assert pointer['owner_id'] == actor.id
    assert set(pointer) == {'pk', 'sk', 'owner_id', 'session_hash', 'created_at'}
    assert 'private-page-token' not in str(connection)
    assert service._decrypt(actor.id, connection) == 'private-page-token'
    assert not service.connection_status(actor, AUTH)['can_select']
    with pytest.raises(HTTPException) as error:
        service.complete_connection(actor, **attempt)
    assert error.value.status_code == 409
    assert service.disconnect(actor) == {'connected': False}
    assert service._connection()[1] is None
    assert service._read(actor.id, service.INTEGRATION_KEY) is None


def test_replaced_attempt_cannot_select(actor, provider):
    connect(actor)
    attempt = begin(actor)
    assert service.complete_connection(actor, **attempt)
    begin(actor)
    with pytest.raises(HTTPException) as error:
        service.select_page(actor, PAGE_ID, AUTH)
    assert error.value.status_code == 409
    assert service.connection_status(actor, AUTH)['connected']


@pytest.mark.parametrize('field, value, status', [('owner_id', 'another-owner', 403), ('status', 'unpublished', 404)])
def test_share_rejects_nonowner_and_draft(actor, article, provider, field, value, status):
    setattr(article, field, value)
    with pytest.raises(HTTPException) as error:
        service.share_article(article, actor, None, str(uuid.uuid4()))
    assert error.value.status_code == status
    provider.assert_not_called()


def test_manage_permission_and_active_account_are_required(actor, provider):
    with pytest.raises(shared_utils.NotAuthorizedError):
        service.connection_status(SimpleNamespace(id='ordinary', status='active', permissions=['regular']), AUTH)
    actor.status = 'banned'
    with pytest.raises(HTTPException) as error:
        service.begin_connection(actor, AUTH)
    assert error.value.status_code == 403
    provider.assert_not_called()


@pytest.mark.parametrize('with_cover', [False, True])
def test_share_uses_preview_and_optional_cover_counts_once(actor, article, provider, storage, with_cover):
    connect(actor)
    if with_cover:
        article.image_filename = 'https://static.example.test/cover.jpg'
    event = str(uuid.uuid4())
    result = service.share_article(article, actor, None, event)
    assert result['post_url'] == 'https://www.facebook.com/' + POST_ID
    assert service.share_article(article, actor, None, event) == result
    feeds = [call.kwargs['form'] for call in provider.call_args_list if call.args[0].endswith('/feed')]
    assert len(feeds) == 1
    message = feeds[0]['message']
    assert 'Public preview' in message and '<p>' not in message
    assert message.count('#SystemDesign') == 1 and '#Caching' in message and '#2026' not in message
    uploads = [call.kwargs['form'] for call in provider.call_args_list if call.args[0].endswith('/photos')]
    if with_cover:
        assert uploads == [{'published': 'false', 'url': article.image_filename}]
        assert json.loads(feeds[0]['attached_media[0]']) == {'media_fbid': '789'}
        assert 'https://example.test/articles/example' in message
    else:
        assert not uploads
        assert feeds[0]['link'] == 'https://example.test/articles/example'
    saved = storage.get_item(Key={'pk': 'POST#' + article.id, 'sk': 'META'}, ConsistentRead=True)['Item']
    assert saved['shares_counts'] == {'facebook_publish': 1}
    assert saved['facebook_shared_at'] > 0
    assert 'updated_at' not in saved


@pytest.mark.parametrize('outcome', ['uncertain', 'wrong-page', 'disconnect-during-upload'])
def test_incomplete_shares_do_not_publish_again_or_count(actor, article, provider, storage, outcome):
    connect(actor)
    graph = provider.side_effect

    def call(path, **args):
        if path.endswith('/feed'):
            if outcome == 'uncertain':
                raise service.FacebookUnconfirmed(502)
            if outcome == 'wrong-page':
                return {'id': '999_456'}
        if path.endswith('/photos') and outcome == 'disconnect-during-upload':
            service.disconnect(actor)
        return graph(path, **args)

    provider.side_effect = call
    event = str(uuid.uuid4())
    if outcome == 'disconnect-during-upload':
        article.image_filename = 'https://static.example.test/cover.jpg'
        with pytest.raises(HTTPException) as error:
            service.share_article(article, actor, None, event)
        assert error.value.status_code == 409
        assert not any(call.args[0].endswith('/feed') for call in provider.call_args_list)
    else:
        for _ in range(2):
            assert service.share_article(article, actor, None, event) == {'shared': False, 'status': 'unconfirmed'}
        assert sum(call.args[0].endswith('/feed') for call in provider.call_args_list) == 1
    saved = storage.get_item(Key={'pk': 'POST#' + article.id, 'sk': 'META'})['Item']
    assert 'shares_counts' not in saved and 'facebook_shared_at' not in saved


def test_local_webp_is_converted_to_jpeg(monkeypatch, tmp_path):
    from PIL import Image
    from io import BytesIO

    Image.new('RGB', (10, 10), color='blue').save(tmp_path / 'example.webp')
    monkeypatch.setattr(service, 'get_static_files_dir', lambda: str(tmp_path))
    monkeypatch.setattr(service, 'get_static_s3_bucket', lambda: '')
    fields, photo = service._cover_for_upload({
        'filename': 'example.webp', 'url': 'https://static.example.test/example.webp',
    })
    assert fields == {'published': 'false'}
    with Image.open(BytesIO(photo)) as converted:
        assert converted.format == 'JPEG'


def test_incomplete_transport_response_cannot_confirm_a_publish(monkeypatch):
    from io import BytesIO

    response = BytesIO(b'{"id":"123_456"}')
    response.status = 202
    opener = Mock(return_value=response)
    monkeypatch.setattr(service, 'open_graph_request', opener)
    monkeypatch.setattr(service, 'config', {
        'facebook_page_app_secret': 'private-app-secret', 'facebook_page_api_version': 'v27.0',
    })
    with pytest.raises(service.FacebookUnconfirmed):
        service.facebook_call('/123/feed', method='POST', token='private-page-token', form={'message': 'Preview'})
    request = opener.call_args.args[0]
    assert request.host == 'graph.facebook.com'
    assert request.get_header('Authorization') == 'Bearer private-page-token'
    assert 'private-page-token' not in request.full_url


def test_confirmed_remote_post_survives_local_history_failure(actor, article, provider, monkeypatch):
    import article_sharing

    connect(actor)
    event = str(uuid.uuid4())
    record = Mock(side_effect=RuntimeError('local history temporarily unavailable'))
    monkeypatch.setattr(article_sharing, 'record_article_share', record)
    assert service.share_article(article, actor, None, event)['post_id'] == POST_ID
    assert service.share_article(article, actor, None, event)['post_id'] == POST_ID
    assert sum(call.args[0].endswith('/feed') for call in provider.call_args_list) == 1


@pytest.mark.parametrize('replaced', [False, True])
def test_erasure_removes_only_the_departing_page_connection(actor, storage, replaced):
    import account_deletion_service as deletion

    pk, sk = service.SITE_KEY
    code = 'facebook-page-' + actor.id
    state_key = {'pk': deletion.PREFIX + code, 'sk': 'META'}
    pointer = {'pk': pk, 'sk': sk, 'owner_id': actor.id, 'session_hash': 'departing'}
    current = {**pointer, 'owner_id': 'new-admin', 'session_hash': 'new'} if replaced else pointer
    storage.put_item(Item={**state_key, 'version': 1})
    storage.put_item(Item=current)
    try:
        deletion._remove(code, {'user_id': actor.id, 'version': 1}, pointer, 'references')
        assert storage.get_item(Key={'pk': pk, 'sk': sk}, ConsistentRead=True).get('Item') == (
            current if replaced else None
        )
    finally:
        storage.delete_item(Key=state_key)


def test_callback_queries_and_publish_counter_are_not_exposed_to_click_tracking():
    path = 'https://api.example.test/integrations/facebook-page/callback'
    assert _access_log_url(path + '?code=secret&state=private') == path
    with pytest.raises(HTTPException) as error:
        validate_share_click({'event_id': str(uuid.uuid4()), 'channel': 'facebook_publish'})
    assert error.value.status_code == 422


def test_api_requires_authentication_and_disables_caching():
    from test_utils import get, get_guest_client, post

    with get_guest_client() as guest:
        response = get(guest, '/integrations/facebook-page')
        assert response.status_code == 401
        assert 'no-store' in response.headers['cache-control']
        response = get(guest, '/integrations/facebook-page/callback?state=fake&code=fake')
        assert response.status_code == 401
        assert response.headers['referrer-policy'] == 'no-referrer'
        response = post(guest, '/articles/11111111-1111-1111-1111-111111111111/share/facebook-page', json={})
        assert response.status_code in (401, 404)
        assert 'no-store' in response.headers['cache-control']
