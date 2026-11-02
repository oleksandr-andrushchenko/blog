"""Account erasure is bounded, resumable, authenticated and provider-verified."""
import base64
import hmac
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import Mock
from uuid import uuid4

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import account_deletion_service as service
import account_media_service as media
import provider_deletion_service as providers
import shared_utils
from web import HTTPException


@pytest.fixture
def account(monkeypatch, tmp_path):
    from test_utils import dynamodb_table, recreate_dynamodb_table

    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(service, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(providers, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    monkeypatch.setitem(service.config, 'dynamodb_table', dynamodb_table.name)
    monkeypatch.setattr(service, 'dispatch', Mock())
    monkeypatch.setattr(service, 'is_prod', lambda: False)
    monkeypatch.setattr(media, 'is_prod', lambda: False)
    monkeypatch.setitem(service.config, 'static_files_dir', str(tmp_path / 'public'))
    monkeypatch.setitem(service.config, 'media_originals_dir', str(tmp_path / 'originals'))
    (tmp_path / 'public').mkdir()
    (tmp_path / 'originals').mkdir()
    uid = 'erase-' + uuid4().hex
    other = 'other-' + uid
    keys = []

    def put(pk, sk='META', **values):
        item = {'pk': pk, 'sk': sk, **values}
        dynamodb_table.put_item(Item=item)
        keys.append({'pk': pk, 'sk': sk})
        return item

    put('USER#' + uid, id=uid, name='Creator', username=uid, status='active', created_at=1)
    put('USER#' + other, id=other, name='Other creator', status='active', created_at=1,
        post_comments_count=1, article_comments_given_count=1, article_likes_given_count=1,
        article_likes_received_count=1, followers_count=1, following_count=1,
        article_comments_received_count=1, comment_likes_given_count=1)
    yield uid, other, put, dynamodb_table, tmp_path
    for key in keys:
        dynamodb_table.delete_item(Key=key)
    page = dynamodb_table.scan()
    for item in page['Items']:
        if item['pk'].startswith(('ACCOUNT_DELETE#', 'DELETE_REQUEST#')) or item['pk'] == service.QUEUE:
            dynamodb_table.delete_item(Key={'pk': item['pk'], 'sk': item['sk']})


def article(put, uid, identifier, **extra):
    return put('POST#' + identifier, id=identifier, user_id=uid, title='Example article',
               post_slug=identifier, content='<p>Make an example</p>',
               status='published', created_at=1, rating_sk=0, **extra)


def run(code):
    service.run_event({'task': 'delete-account', 'payload': {'code': code}})


def test_large_erasure_removes_contributions_aliases_relations_and_repairs_counters(account, monkeypatch):
    uid, other, put, table, directory = account
    pid = uid + '-article'
    other_pid = other + '-article'
    category = uid + '-category'
    tag = uid + '-tag'
    put('CATEGORY', category, published_articles_count=126)
    put('POST_TAG#' + tag, rating_sk=1, posts_count=1)
    article(put, uid, pid, category=category, tags=[tag],
           result_files=[{'filename': '11111111-1111-4111-8111-111111111111.png', 'format': 'image'}, {'filename': '22222222-2222-4222-8222-222222222222.png', 'format': 'image'}])
    article(put, other, other_pid, comments_count=1, likes_count=1,
           result_files=[{'filename': '22222222-2222-4222-8222-222222222222.png', 'format': 'image'}])
    # Exceed both the 100-item transaction and common first-page boundaries.
    for number in range(125):
        article(put, uid, f'{uid}-{number}', category=category)
    put('POST#' + pid, 'COMMENT#other-comment', user_id=other, post_id=pid, id='other-comment', text='Other comment')
    put('POST#' + other_pid, 'COMMENT#own-comment', user_id=uid, post_id=other_pid, id='own-comment', text='Own comment')
    put('POST#' + pid, 'IMP#' + other, post_id=pid, user_id=other, action='like')
    put('POST#' + other_pid, 'IMP#' + uid, post_id=other_pid, user_id=uid, action='like')
    put('POST#' + other_pid, 'COMMENT_IMP#own-comment#' + other,
        post_id=other_pid, comment_id='own-comment', user_id=other, owner_id=uid, action='like')
    put('USER#' + uid, 'REL#' + other, user_id=uid, target_user_id=other, action='follow')
    put('USER#' + other, 'REL#' + uid, user_id=other, target_user_id=uid, action='follow')
    put('PROVIDER_USER#pool#' + uid, user_id=uid)
    put('USER_SLUG#' + uid, user_id=uid)
    put('USER_SLUG#old-' + uid, user_id=uid)
    put('USER_REDIRECT#old-' + uid, username='old-' + uid, redirect_to=uid)
    put(f'POST_SLUG#historic-{uid}', post_id=pid)
    put('POST_TAG_COMBO#' + tag, 'POST#1#' + pid, post_id=pid)
    put('USER_ACTIVITY#' + other, 'ACTIVITY#example', actor_user_id=other, entity_owner_id=uid,
        entity_type='article', entity_id=pid)
    put('USER_ACTIVITY#' + other, 'ACTIVITY#comment', actor_user_id=other, entity_owner_id=other,
        entity_type='comment', entity_id='other-comment', entity_url=f'/articles/{pid}#comment-other-comment',
        entity_title='Private old article title')
    put('USER#' + uid, 'INTEGRATION#threads', threads_user_id='123', encrypted_token='secret')
    put('CONTACT_MESSAGE#' + uid, user_id=uid, email='erased@example.com', message='Private')
    put('IMPORTED#test', uid, created_at=1)  # Non-identifying provenance remains.
    for root in ('public', 'originals'):
        for filename in ('11111111-1111-4111-8111-111111111111.png', '22222222-2222-4222-8222-222222222222.png'):
            (directory / root / filename).write_bytes(b'example')
    code = service.request_deletion(uid)
    assert service.receipt(code)['state'] == 'pending'
    assert shared_utils.find_user(uid) is None
    assert shared_utils.find_article(pid) is None
    sizes = []
    write = service.dynamodb_transact_write

    def bounded(writes):
        sizes.append(len(writes))
        write(writes)

    monkeypatch.setattr(service, 'dynamodb_transact_write', bounded)
    run(code)
    run(code)  # Duplicate delivery must be harmless.
    assert max(sizes) < 100
    assert set(service.receipt(code)) == {'pk', 'sk', 'state', 'created_at', 'completed_at'}
    assert service.receipt(code)['state'] == 'complete'
    assert table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'META'}).get('Item') is None
    assert table.get_item(Key={'pk': 'PROVIDER_USER#pool#' + uid, 'sk': 'META'}).get('Item') is None
    assert table.get_item(Key={'pk': 'USER_REDIRECT#old-' + uid, 'sk': 'META'}).get('Item') is None
    assert table.get_item(Key={'pk': 'USER_ACTIVITY#' + other, 'sk': 'ACTIVITY#comment'}).get('Item') is None
    assert table.get_item(Key={'pk': 'IMPORTED#test', 'sk': uid})['Item']['created_at'] == 1
    survivor = table.get_item(Key={'pk': 'USER#' + other, 'sk': 'META'})['Item']
    for field in ('post_comments_count', 'article_comments_given_count', 'article_comments_received_count',
                  'article_likes_given_count', 'article_likes_received_count', 'comment_likes_given_count',
                  'followers_count', 'following_count'):
        assert survivor[field] == 0, (field, survivor)
    remaining = table.get_item(Key={'pk': 'POST#' + other_pid, 'sk': 'META'})['Item']
    assert remaining['likes_count'] == remaining['comments_count'] == 0
    assert table.get_item(Key={'pk': 'CATEGORY', 'sk': category})['Item']['published_articles_count'] == 0
    assert table.get_item(Key={'pk': 'POST_TAG#' + tag, 'sk': 'META'})['Item']['posts_count'] == 0
    assert not (directory / 'public' / '11111111-1111-4111-8111-111111111111.png').exists()
    assert not (directory / 'originals' / '11111111-1111-4111-8111-111111111111.png').exists()
    assert (directory / 'public' / '22222222-2222-4222-8222-222222222222.png').exists()


def test_failed_transaction_and_budget_resume_without_double_decrements(account, monkeypatch):
    uid, other, put, table, _ = account
    pid = uid + '-article'
    article(put, other, pid, likes_count=1)
    put('POST#' + pid, 'IMP#' + uid, post_id=pid, user_id=uid, action='like')
    code = service.request_deletion(uid)
    service.run_event({'task': 'delete-account', 'payload': {'code': code}}, budget_seconds=0)
    assert service.receipt(code)['phase'] == 'articles'
    original = service.dynamodb_transact_write
    failed = False

    def fail_once(writes):
        nonlocal failed
        if not failed and writes[0].get('Delete', {}).get('Key', {}).get('sk') == 'IMP#' + uid:
            failed = True
            raise RuntimeError('Temporary storage failure')
        original(writes)

    monkeypatch.setattr(service, 'dynamodb_transact_write', fail_once)
    with pytest.raises(RuntimeError, match='Temporary'):
        run(code)
    assert service.receipt(code)['state'] == 'pending'
    run(code)
    assert service.receipt(code)['state'] == 'complete'
    assert table.get_item(Key={'pk': 'POST#' + pid, 'sk': 'META'})['Item']['likes_count'] == 0


def sign(payload, secret='test-secret'):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    signature = base64.urlsafe_b64encode(hmac.digest(secret.encode(), encoded.encode(), 'sha256')).decode().rstrip('=')
    return signature + '.' + encoded


@pytest.mark.parametrize('case', ['signature', 'algorithm', 'future', 'subject', 'malformed'])
def test_meta_requests_require_valid_signatures_and_claims(monkeypatch, case):
    monkeypatch.setitem(providers.config, 'threads_app_secret', 'test-secret')
    payload = {'algorithm': 'HMAC-SHA256', 'user_id': '12345', 'issued_at': int(time.time())}
    if case == 'algorithm':
        payload['algorithm'] = 'none'
    if case == 'future':
        payload['issued_at'] += 600
    if case == 'subject':
        payload['user_id'] = '../another-account'
    token = sign(payload, 'wrong-secret' if case == 'signature' else 'test-secret')
    if case == 'malformed':
        token = 'garbage'
    with pytest.raises(HTTPException) as error:
        providers.verified_meta_request('threads', token)
    assert error.value.status_code == 400


def test_signed_callback_is_queued_and_replays_do_not_resolve_a_new_account(account, monkeypatch):
    uid, _, put, _, _ = account
    monkeypatch.setitem(providers.config, 'threads_app_secret', 'test-secret')
    lookup = Mock(return_value=({uid}, None))
    monkeypatch.setattr(providers, '_threads_account_page', lookup)
    token = sign({'algorithm': 'HMAC-SHA256', 'user_id': '12345', 'issued_at': int(time.time())})
    code = providers.accept_meta_request('threads', token)
    lookup.assert_not_called()  # HTTP handler performs no directory/table scan.
    run(code)
    target = service.receipt(code)['code']
    run(target)
    assert service.receipt(target)['state'] == 'complete'
    assert providers.accept_meta_request('threads', token) == code
    assert lookup.call_count == 1
    assert 'subject' not in service.receipt(code)


def test_owner_confirmation_and_cross_account_access(account):
    uid, other, _, _, _ = account
    user = shared_utils.user_from_dynamodb({'id': uid, 'name': 'Owner', 'created_at': 1})
    stranger = shared_utils.user_from_dynamodb({'id': other, 'name': 'Other', 'created_at': 1})
    with pytest.raises(HTTPException) as error:
        service.delete_own_account(user, stranger, {'confirmation': 'DELETE'})
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        service.delete_own_account(user, user, {'confirmation': 'delete'})
    assert error.value.status_code == 422


def test_http_owner_flow_and_private_status():
    from test_utils import API_TEST_BASE_URL, dynamodb_table, get_logged_in_client, get_guest_client, get, post

    identity = {'sub': 'delete-http-' + uuid4().hex, 'iss': 'erase-http', 'email': uuid4().hex + '@example.com'}
    client = get_logged_in_client(identity)
    mapping = dynamodb_table.get_item(Key={'pk': f"PROVIDER_USER#{identity['iss']}#{identity['sub']}", 'sk': 'META'})['Item']
    uid = mapping['user_id']
    settings = get(client, f'/users/{uid}/edit')
    assert settings.status_code == 200
    assert 'Type DELETE to confirm' in settings.text
    assert f'data-deletion-url="{API_TEST_BASE_URL}/users/{uid}/deletion"' in settings.text
    assert get(get_guest_client(), '/account-deletion').status_code == 200
    response = post(client, f'/users/{uid}/deletion', json={'confirmation': 'DELETE'},
                           headers={'Origin': 'https://evil.example'}, timeout=30)
    assert response.status_code == 403
    response = post(client, f'/users/{uid}/deletion', json={'confirmation': 'DELETE'}, timeout=30)
    assert response.status_code == 202, response.text
    code = response.json()['confirmation_code']
    for _ in range(100):
        state = dynamodb_table.get_item(Key={'pk': 'ACCOUNT_DELETE#' + code, 'sk': 'META'}).get('Item')
        if state and state['state'] == 'complete':
            break
        time.sleep(.1)
    assert state['state'] == 'complete'
    status = get(get_guest_client(), '/account-deletion/status/' + code)
    assert status.status_code == 200
    assert status.headers['Cache-Control'] == 'no-store'
    assert status.headers['Referrer-Policy'] == 'no-referrer'
    assert uid not in status.text and identity['email'] not in status.text
    assert 'Your deletion request is complete' in status.text
    assert 'googletagmanager.com' not in status.text
    assert get(client, f'/users/{uid}/edit').status_code in (401, 404)


def test_variant_keys_cover_original_and_all_generated_sizes():
    keys = media.upload_keys('example_1200x800.png')
    assert keys == {'example_1200x800.png', 'example_1200x800.webp', 'example_160x107.webp',
                    'example_320x213.webp', 'example_640x427.webp', 'example_1024x683.webp'}


def test_recovery_uses_bounded_outbox_query_and_removes_finished_jobs(account, monkeypatch):
    uid, _, _, table, _ = account
    code = service.request_deletion(uid)
    service.dispatch.reset_mock()
    service.recover_event({'task': 'recover-account-deletions'})
    assert any(call.args == (code,) for call in service.dispatch.call_args_list)
    run(code)
    service.dispatch.reset_mock()
    service.recover_event({'task': 'recover-account-deletions'})
    assert not any(call.args == (code,) for call in service.dispatch.call_args_list)
    assert table.get_item(Key={'pk': service.QUEUE, 'sk': code}).get('Item') is None


def test_pending_account_cannot_sign_in_or_finish_an_inflight_write(account):
    from basic_dtos import UserTokenDTO
    from shared_utils import DynamoDBTransactionError, add_dynamodb_user_update_transact, dynamodb_transact_write

    uid, _, put, _, _ = account
    put('PROVIDER_USER#pool#subject-' + uid, user_id=uid)
    actor = shared_utils.find_user(uid)
    writes = []
    add_dynamodb_user_update_transact(writes, actor, {'name': 'An in-flight edit'})
    service.request_deletion(uid)
    token = UserTokenDTO(sub='subject-' + uid, iss='pool')
    assert shared_utils.get_user_by_user_token(token) is None
    with pytest.raises(shared_utils.InvalidTokenError):
        shared_utils.upsert_user_by_user_token(token, None)
    with pytest.raises(DynamoDBTransactionError):
        dynamodb_transact_write(writes)


def test_facebook_uses_verified_directory_subject_never_email(account, monkeypatch):
    uid, _, put, _, _ = account
    monkeypatch.setitem(providers.config, 'cognito_user_pool_id', 'pool')
    directory = Mock()
    directory.admin_get_user.return_value = {'UserAttributes': [
        {'Name': 'sub', 'Value': 'directory-sub'}, {'Name': 'email', 'Value': 'untrusted@example.com'}]}
    monkeypatch.setattr(providers.boto3, 'client', Mock(return_value=directory))
    put('PROVIDER_USER#pool#directory-sub', user_id=uid, created_at=1000)
    assert providers._facebook_account('12345', 2000) == uid
    directory.admin_get_user.assert_called_once_with(UserPoolId='pool', Username='Facebook_12345')
    assert providers._facebook_account('12345', 999) is None


def test_cognito_cleanup_handles_every_link_and_missing_users(monkeypatch):
    monkeypatch.setattr(service, 'is_prod', lambda: True)
    monkeypatch.setitem(service.config, 'cognito_user_pool_id', 'configured-pool')
    directory = Mock()
    directory.list_users.return_value = {'Users': [{'Username': 'Google_subject'}]}
    directory.exceptions.UserNotFoundException = type('MissingUser', (Exception,), {})
    factory = Mock(return_value=directory)
    monkeypatch.setattr(service.boto3, 'client', factory)
    service._delete_cognito_identity('PROVIDER_USER#configured-pool#directory-sub')
    directory.admin_user_global_sign_out.assert_called_once_with(UserPoolId='configured-pool', Username='Google_subject')
    directory.admin_delete_user.assert_called_once_with(UserPoolId='configured-pool', Username='Google_subject')
    factory.reset_mock()
    service._delete_cognito_identity('PROVIDER_USER#legacy-issuer#subject')
    factory.assert_not_called()


def test_status_urls_are_redacted_in_access_logs():
    from notifications import _access_log_url

    assert _access_log_url('https://example.com/account-deletion/status/secret?code=private') == (
        'https://example.com/account-deletion/status/[private]')


def test_erasure_does_not_remove_site_assets_referenced_as_results(account):
    _, _, _, _, directory = account
    asset = directory / 'public' / 'scripts.js'
    asset.write_bytes(b'application code')
    assert media.erase_upload('scripts.js') == []
    assert asset.read_bytes() == b'application code'


def test_html_article_media_and_shared_avatars_are_included_in_reference_checks():
    assert set(service._files({
        'avatar_filename': 'avatar.webp',
        'content': '<p><img src="https://static.example/image.webp"></p>'
                   '<video poster="poster.webp"><source src="clip.mp4"></video>'
                   '<a href="/attachment.pdf">Download</a>',
    })) == {'avatar.webp', 'image.webp', 'poster.webp', 'clip.mp4', 'attachment.pdf'}


def test_cdn_cleanup_is_batched_and_receipt_waits_for_completion(account, monkeypatch):
    uid, _, put, _, _ = account
    code = service.request_deletion(uid)
    state = service.receipt(code)
    # Exercise the cloud boundary using synthetic files and mocked AWS only.
    put(service.PREFIX + code, 'FILE#11111111-1111-4111-8111-111111111111.png')
    put(service.PREFIX + code, phase='media', user_id=uid, state='pending',
        version=state['version'], created_at=state['created_at'])
    monkeypatch.setattr(service, 'is_prod', lambda: True)
    monkeypatch.setitem(service.config, 'cloudfront_distribution_id', 'test-distribution')
    cloud = Mock()
    cloud.create_invalidation.return_value = {'Invalidation': {'Id': 'synthetic-invalidation'}}
    cloud.get_invalidation.return_value = {'Invalidation': {'Status': 'InProgress'}}
    monkeypatch.setattr(service.boto3, 'client', Mock(return_value=cloud))
    service._media(code, service.receipt(code))
    assert cloud.create_invalidation.call_count == 1
    assert service.receipt(code)['phase'] == 'cache'
    assert service._cache(code, service.receipt(code)) is False
    assert service.receipt(code)['state'] == 'pending'
    cloud.get_invalidation.return_value = {'Invalidation': {'Status': 'Completed'}}
    assert service._cache(code, service.receipt(code)) is True
    assert service.receipt(code)['phase'] == 'finish'


def test_deletion_lifecycle_logs_once_without_account_or_receipt_credentials(account, caplog):
    uid, _, _, _, _ = account
    code = service.request_deletion(uid)
    assert service.request_deletion(uid) == code
    run(code)
    run(code)
    records = [record for record in caplog.records if record.getMessage().startswith('Account deletion ')]
    assert [record.getMessage() for record in records] == [
        'Account deletion requested', 'Account deletion started', 'Account deletion completed']
    assert all(not getattr(record, 'context', None) for record in records)
    serialized = json.dumps([getattr(record, 'context', {}) for record in records])
    assert uid not in serialized and code not in serialized


def test_erasure_removes_conversation_history_peer_pointers_and_private_notifications(account):
    from direct_messages import message_partition
    uid, other, put, table, _ = account
    partition = message_partition(uid, other)
    message_key = 'MESSAGE#00000000000000000001#00000000-0000-4000-8000-000000000001'
    put(partition, participant_ids=[uid, other], created_at=1, revision=1)
    put(partition, message_key, sender_id=other, recipient_id=uid, text='Private erasure content')
    put(partition, 'RECEIPT#receipt', sender_id=other, fingerprint='private-hash', message_key=message_key)
    put(f'USER#{other}', f'DM_INBOX#00000000000000000001#{uid}', peer_id=uid, preview='Private erasure content')
    put(f'USER#{other}', f'DM_READ#{uid}', last_read_key=message_key)
    put(f'USER#{uid}', 'SITE_NOTIFICATION#00000000000000000001#alert', message='Private alert')
    put(f'USER#{uid}', 'SITE_NOTIFICATION_ID#alert', notification_key='private-key')
    code = service.request_deletion(uid)
    run(code)
    for pk, sk in ((partition, 'META'), (partition, message_key), (partition, 'RECEIPT#receipt'),
                   (f'USER#{other}', f'DM_INBOX#00000000000000000001#{uid}'),
                   (f'USER#{other}', f'DM_READ#{uid}'),
                   (f'USER#{uid}', 'SITE_NOTIFICATION#00000000000000000001#alert')):
        assert 'Item' not in table.get_item(Key={'pk': pk, 'sk': sk})
    assert table.get_item(Key={'pk': f'USER#{other}', 'sk': 'META'})['Item']['name'] == 'Other creator'
