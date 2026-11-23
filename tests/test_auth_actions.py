"""Guest actions cross normal sign-in, then execute once through the API."""
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest
from pyquery import PyQuery

from test_bookmarks import library
from test_utils import (
    WEB_TEST_BASE_URL, dynamodb_table, encode_offset, get,
    get_dynamodb_user_by_email, get_guest_client, get_logged_in_client, post,
)

HEADERS = {'Origin': WEB_TEST_BASE_URL}


def start(client, action, pid=None, *, destination='/articles', tags=None):
    body = {'action': action, 'target': pid, 'return_url': destination}
    if tags is not None:
        body['tags'] = tags
    response = post(client, '/auth/actions', json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert 'no-store' in response.headers['Cache-Control']
    return parse_qs(urlsplit(response.json()['login_url']).query)


def callback(client, params, identity, *, binding=None):
    # The isolated services use different Docker hostnames; browsers use the
    # configured common cookie domain. Transfer only the test's own browser proof.
    cookie = binding if binding is not None else client.cookies.get('login_browser')
    return client.get(WEB_TEST_BASE_URL + '/login-callback',
                      params={'state': params['state'][0], 'code': encode_offset(identity)},
                      headers={'Cookie': 'login_browser=' + (cookie or '')},
                      allow_redirects=False, timeout=30)


def complete(client, **body):
    return post(client, '/auth/actions/complete', json=body, headers=HEADERS)


def acknowledge(client):
    return post(client, '/auth/welcome/seen', json={}, headers=HEADERS)


@pytest.mark.parametrize('action', ['bookmark', 'like', 'dislike'])
def test_guest_action_completes_after_signup_before_one_time_welcome(library, action):
    _, _, _, ids = library
    guest = get_guest_client()
    identity = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    params = start(guest, action, ids[0], destination='/articles/' + ids[0])
    assert params['code_challenge_method'] == ['S256']
    assert params['nonce'] == params['state']
    response = callback(guest, params, identity)
    assert response.status_code == 302, response.text
    assert response.headers['location'] == '/auth/complete'
    uid = get_dynamodb_user_by_email(identity['email'])['id']
    before = dynamodb_table.get_item(Key={'pk': 'POST#' + ids[0], 'sk': 'META'})['Item']
    assert before.get('bookmarks_count', 0) == before.get('likes_count', 0) == before.get('dislikes_count', 0) == 0
    page = get(guest, '/auth/complete')
    assert page.status_code == 200, page.text
    assert 'no-store' in page.headers['Cache-Control']
    assert PyQuery(page.text)('#registration-welcome').length == 1
    result = complete(guest)
    assert result.status_code == 200, result.text
    assert result.json() == {'redirect_url': '/articles/' + ids[0], 'welcome': True}
    field = {'bookmark': 'bookmarks_count', 'like': 'likes_count', 'dislike': 'dislikes_count'}[action]
    assert dynamodb_table.get_item(Key={'pk': 'POST#' + ids[0], 'sk': 'META'})['Item'][field] == 1
    retry = complete(guest)
    assert retry.status_code == 200, retry.text
    assert retry.json()['welcome'] is True  # Completion is not proof that the popup appeared.
    assert acknowledge(guest).status_code == 200
    assert complete(guest).json()['welcome'] is False
    assert dynamodb_table.get_item(Key={'pk': 'POST#' + ids[0], 'sk': 'META'})['Item'][field] == 1
    pending = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'LOGIN_CONTINUATION'})['Item']
    assert pending['done'] is True and 'intent' not in pending and 'welcome' not in pending


@pytest.mark.parametrize('action', ['bookmark', 'like', 'dislike'])
def test_returning_user_desired_action_never_toggles_existing_state(library, action):
    (reader, uid), _, _, ids = library
    dynamodb_table.update_item(Key={'pk': 'USER#' + uid, 'sk': 'META'},
                              UpdateExpression='SET welcome_seen_at = :seen REMOVE welcome_state',
                              ExpressionAttributeValues={':seen': 1})
    profile = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'META'})['Item']
    provider, identity = next(iter(profile['providers'].items()))
    proof = {'sub': identity['sub'], 'iss': provider, 'email': profile['user_email_pk']}
    if action == 'bookmark':
        assert post(reader, '/bookmarks/' + ids[0], json={'saved': True}).status_code == 200
    else:
        assert post(reader, '/articles/' + ids[0] + '/impression', json={'action': action}).status_code == 200
    original = dynamodb_table.get_item(Key={'pk': 'POST#' + ids[0], 'sk': 'META'})['Item']
    guest = get_guest_client()
    response = callback(guest, start(guest, action, ids[0]), proof)
    assert response.status_code == 302, response.text
    result = complete(guest)
    assert result.status_code == 200, result.text
    assert result.json()['welcome'] is False
    assert dynamodb_table.get_item(Key={'pk': 'POST#' + ids[0], 'sk': 'META'})['Item'] == original


def test_subscription_is_completed_once_and_preserves_combined_topics(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    params = start(guest, 'subscribe', tags=['portrait', 'lighting'])
    assert callback(guest, params, proof).status_code == 302
    result = complete(guest)
    assert result.status_code == 200, result.text
    assert result.json()['welcome'] is True
    complete(guest)
    subscriptions = get(guest, '/tag-subscriptions').json()
    assert len(subscriptions) == 1
    assert subscriptions[0]['tags'] == ['lighting', 'portrait']


def test_browser_binding_and_callback_replay_rejected(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    params = start(guest, 'bookmark', library[3][0])
    binding = guest.cookies.get('login_browser')
    assert callback(guest, params, proof, binding='wrong').status_code == 400
    assert callback(guest, params, proof, binding=binding).status_code == 302
    assert callback(guest, params, proof, binding=binding).status_code == 400


def test_failed_action_can_be_skipped_before_welcome(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    params = start(guest, 'bookmark', 'missing-article')
    assert callback(guest, params, proof).status_code == 302
    assert complete(guest).status_code == 404
    result = complete(guest, skip=True)
    assert result.status_code == 200, result.text
    assert result.json()['welcome'] is True
    assert acknowledge(guest).status_code == 200
    assert complete(guest).json()['welcome'] is False


@pytest.mark.parametrize('destination', ['//external.example/', 'https://external.example/', '/%5cexternal.example/', '/auth/complete'])
def test_unsafe_or_looping_destinations_fall_back_home(library, destination):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    params = start(guest, 'bookmark', library[3][0], destination=destination)
    assert callback(guest, params, proof).status_code == 302
    result = complete(guest)
    assert result.status_code == 200, result.text
    assert result.json()['redirect_url'] == '/'


def test_action_cannot_be_injected_through_login_query_or_foreign_origin(library):
    guest = get_guest_client()
    assert post(guest, '/auth/actions', json={'action': 'bookmark', 'target': library[3][0]},
                headers={'Origin': 'https://external.example'}).status_code == 403
    assert post(guest, '/auth/actions', json={'action': 'delete-account'}, headers=HEADERS).status_code == 422
    for invalid in (None, [], {}, 42):
        assert post(guest, '/auth/actions', json={'action': invalid}, headers=HEADERS).status_code == 422
    response = get(guest, '/login', params={'action': 'bookmark', 'target': library[3][0]}, allow_redirects=False)
    assert response.status_code == 307
    assert 'action' not in parse_qs(urlsplit(response.headers['location']).query)


def test_plain_signup_also_receives_welcome_but_next_login_does_not(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    login = get(guest, '/login', params={'redirect_url': '/articles/new'}, allow_redirects=False)
    params = parse_qs(urlsplit(login.headers['location']).query)
    response = callback(guest, params, proof)
    assert response.status_code == 302, response.text
    assert response.headers['location'] == '/auth/complete'
    result = complete(guest)
    assert result.status_code == 200, result.text
    assert result.json() == {'redirect_url': '/articles/new', 'welcome': True}
    assert acknowledge(guest).status_code == 200
    returning = get_guest_client()
    login = get(returning, '/login', params={'redirect_url': '/articles/new'}, allow_redirects=False)
    response = callback(returning, parse_qs(urlsplit(login.headers['location']).query), proof)
    assert response.status_code == 302, response.text
    assert response.headers['location'] == '/articles/new'
    assert complete(returning).status_code == 409


def test_cancelled_login_does_not_register_or_complete_an_action(library):
    guest = get_guest_client()
    params = start(guest, 'bookmark', library[3][0], destination='/tags')
    binding = guest.cookies.get('login_browser')
    response = guest.get(WEB_TEST_BASE_URL + '/login-callback',
                         params={'state': params['state'][0], 'error': 'access_denied'},
                         headers={'Cookie': 'login_browser=' + binding}, allow_redirects=False, timeout=30)
    assert response.status_code == 302, response.text
    assert response.headers['location'] == '/tags'
    assert not response.cookies.get('token')
    assert complete(guest).status_code == 401


def test_expired_or_other_session_continuation_cannot_mutate(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    assert callback(guest, start(guest, 'bookmark', library[3][0]), proof).status_code == 302
    uid = get_dynamodb_user_by_email(proof['email'])['id']
    another = get_logged_in_client(proof)
    assert complete(another).status_code == 409
    dynamodb_table.update_item(Key={'pk': 'USER#' + uid, 'sk': 'LOGIN_CONTINUATION'},
                              UpdateExpression='SET expires_at = :expired',
                              ExpressionAttributeValues={':expired': 1})
    assert complete(guest).status_code == 409
    assert not dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'BOOKMARK#' + library[3][0]}).get('Item')


def test_expired_login_is_rejected_before_registration(library):
    from hashlib import sha256

    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    params = start(guest, 'bookmark', library[3][0])
    key = {'pk': 'LOGIN_FLOW#' + sha256(params['state'][0].encode()).hexdigest(), 'sk': 'META'}
    dynamodb_table.update_item(Key=key, UpdateExpression='SET expires_at = :expired',
                              ExpressionAttributeValues={':expired': 1})
    assert callback(guest, params, proof).status_code == 400
    assert not dynamodb_table.get_item(Key={'pk': 'PROVIDER_USER#auth-actions#' + proof['sub'], 'sk': 'META'}).get('Item')


def test_duplicate_completions_are_atomic_and_cannot_reverse_a_later_manual_change(library):
    from concurrent.futures import ThreadPoolExecutor

    guest = get_guest_client()
    pid = library[3][0]
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    assert callback(guest, start(guest, 'like', pid), proof).status_code == 302
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: complete(guest), range(2)))
    assert any(response.status_code == 200 for response in results)
    assert all(response.status_code in {200, 409} for response in results)
    item = lambda: dynamodb_table.get_item(Key={'pk': 'POST#' + pid, 'sk': 'META'})['Item']
    assert item()['likes_count'] == 1
    assert post(guest, '/articles/' + pid + '/impression', json={'action': 'like'}).status_code == 200
    assert item()['likes_count'] == 0
    assert complete(guest).status_code == 200
    assert item()['likes_count'] == 0


def test_account_status_change_blocks_the_pending_action(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    assert callback(guest, start(guest, 'bookmark', library[3][0]), proof).status_code == 302
    uid = get_dynamodb_user_by_email(proof['email'])['id']
    dynamodb_table.update_item(Key={'pk': 'USER#' + uid, 'sk': 'META'}, UpdateExpression='SET #status = :banned',
                              ExpressionAttributeNames={'#status': 'status'}, ExpressionAttributeValues={':banned': 'banned'})
    assert complete(guest).status_code == 403
    assert not dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'BOOKMARK#' + library[3][0]}).get('Item')


def test_welcome_is_account_owned_survives_abandoned_display_and_acknowledges_once(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    assert callback(guest, start(guest, 'bookmark', library[3][0]), proof).status_code == 302
    uid = get_dynamodb_user_by_email(proof['email'])['id']
    key = {'pk': 'USER#' + uid, 'sk': 'META'}
    account = lambda: dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item']
    assert account()['welcome_state'] == 'pending'
    assert acknowledge(guest).status_code == 409  # Cannot mark seen before the action finishes.
    assert 'welcome_pending: false' in get(guest, '/').text
    assert complete(guest).json()['welcome'] is True
    before = account()
    assert before['welcome_state'] == 'ready' and 'welcome_seen_at' not in before
    assert 'welcome_pending: true' in get(guest, '/').text
    # Closing before the modal opens must survive even deletion/expiry of the login record.
    dynamodb_table.delete_item(Key={'pk': 'USER#' + uid, 'sk': 'LOGIN_CONTINUATION'})
    returning = get_guest_client()
    login = get(returning, '/login', params={'redirect_url': '/tags'}, allow_redirects=False)
    assert callback(returning, parse_qs(urlsplit(login.headers['location']).query), proof).headers['location'] == '/auth/complete'
    assert complete(returning).json()['welcome'] is True
    before_seen = account()
    assert acknowledge(returning).json() == {'seen': True}
    after = account()
    assert 'welcome_state' not in after
    assert after['welcome_seen_at'] > 0
    assert {k: v for k, v in before_seen.items() if k != 'welcome_state'} == {
        k: v for k, v in after.items() if k != 'welcome_seen_at'}
    assert acknowledge(returning).json() == {'seen': True}
    assert account() == after
    assert complete(returning).json()['welcome'] is False
    assert 'welcome_pending: false' in get(returning, '/').text


def test_existing_accounts_are_not_enrolled_and_cannot_acknowledge_another_user(library):
    (reader, uid), _, _, _ = library
    dynamodb_table.update_item(Key={'pk': 'USER#' + uid, 'sk': 'META'}, UpdateExpression='REMOVE welcome_state')
    before = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'META'})['Item']
    assert acknowledge(reader).status_code == 409
    assert post(reader, '/auth/welcome/seen', json={}, headers={'Origin': 'https://external.example'}).status_code == 403
    after = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'META'})['Item']
    assert before == after


def test_failed_action_keeps_welcome_pending_until_explicit_skip(library):
    guest = get_guest_client()
    proof = {'sub': uuid.uuid4().hex, 'iss': 'auth-actions', 'email': uuid.uuid4().hex + '@example.com'}
    assert callback(guest, start(guest, 'bookmark', 'missing-article'), proof).status_code == 302
    assert complete(guest).status_code == 404
    assert acknowledge(guest).status_code == 409
    assert 'welcome_pending: false' in get(guest, '/').text
    assert complete(guest, skip=True).json()['welcome'] is True
    assert 'welcome_pending: true' in get(guest, '/').text


@pytest.mark.parametrize('action', ['like', 'dislike', 'subscribe'])
def test_continuation_does_not_mutate_for_a_stale_active_user_after_ban(library, action):
    from types import SimpleNamespace
    from auth_action_service import complete_action
    from login_flow import digest
    from shared_utils import user_from_dynamodb, utc_now
    from web import HTTPException

    (reader, uid), _, _, ids = library
    profile = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'META'})['Item']
    stale_user = user_from_dynamodb(profile)
    intent = {'action': action, 'target': ids[0]} if action != 'subscribe' else {'action': action, 'tags': ['guarded']}
    dynamodb_table.put_item(Item={'pk': 'USER#' + uid, 'sk': 'LOGIN_CONTINUATION',
        'auth': digest('test-token'), 'expires_at': utc_now() + 60000,
        'return_url': '/articles', 'intent': intent})
    dynamodb_table.update_item(Key={'pk': 'USER#' + uid, 'sk': 'META'},
        UpdateExpression='SET #status = :banned', ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={':banned': 'banned'})
    from shared_utils import SlugDuplicationError
    with pytest.raises((HTTPException, SlugDuplicationError)):
        complete_action(stale_user, SimpleNamespace(cookies={'token': 'test-token'}), {})
    # The rejected transaction leaves both the desired action and continuation uncommitted.
    pending = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'LOGIN_CONTINUATION'})['Item']
    assert not pending.get('done')
    article = dynamodb_table.get_item(Key={'pk': 'POST#' + ids[0], 'sk': 'META'})['Item']
    assert article.get('likes_count', 0) == article.get('dislikes_count', 0) == 0
