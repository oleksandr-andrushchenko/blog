"""Private bookmarks exercise real HTTP, isolated DynamoDB and guarded writes."""
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import Mock

import pytest
from pyquery import PyQuery

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT / 'shared'), str(ROOT / 'api-lambda')]

import bookmark_service
from bookmarks import bookmark_order_key
from test_utils import (
    dynamodb_table, get, post, get_guest_client, get_logged_in_client,
    get_dynamodb_user_by_email, recreate_dynamodb_table, WEB_TEST_BASE_URL,
)
from web import HTTPException


@pytest.fixture
def library():
    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    dynamodb_table.put_item(Item={
        'pk': 'CATEGORY', 'sk': 'bookmarks-test', 'category_slug': 'bookmarks-test',
        'name': 'Bookmark Examples', 'published_articles_count': 0,
    })
    people = []
    for name in ('Reader', 'Author', 'Other'):
        identifier = uuid.uuid4().hex
        identity = {'sub': identifier, 'iss': 'bookmarks-test', 'email': identifier + '@example.com', 'name': name}
        client = get_logged_in_client(identity)
        people.append((client, get_dynamodb_user_by_email(identity['email'])['id']))
    reader, author, other = people
    ids = [uuid.uuid4().hex for _ in range(3)]
    for index, pid in enumerate(ids):
        dynamodb_table.put_item(Item={
            'pk': 'POST#' + pid, 'sk': 'META', 'id': pid, 'user_id': author[1],
            'user_name': 'Author', 'title': f'Bookmark example {index}', 'post_slug': pid,
            'content': '<p>Write a useful example.</p>',
            'status': 'published', 'created_at': 1, 'rating_sk': 0,
            'category': 'bookmarks-test',
        })
    return reader, author, other, ids


def private(response, status=200):
    assert response.status_code == status, response.text
    assert 'no-store' in response.headers.get('Cache-Control', '')
    return response


def test_bookmarks_are_private_idempotent_and_keep_content_unchanged(library):
    (reader, uid), (author, owner), (other, _), ids = library
    pid = ids[0]
    before_article = dynamodb_table.get_item(Key={'pk': 'POST#' + pid, 'sk': 'META'})['Item']
    before_user = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'META'})['Item']
    path = '/bookmarks/' + pid
    assert private(post(reader, path, json={'saved': True})).json() == {'saved': True, 'bookmarks_count': 1}
    pointer = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'BOOKMARK#' + pid})['Item']
    assert set(pointer) == {'pk', 'sk', 'created_at'}
    assert private(post(reader, path, json={'saved': True})).json() == {'saved': True, 'bookmarks_count': 1}
    assert dynamodb_table.get_item(Key={'pk': pointer['pk'], 'sk': pointer['sk']})['Item'] == pointer
    assert private(post(reader, '/bookmarks/status', json={'article_ids': ids})).json() == {
        ids[0]: True, ids[1]: False, ids[2]: False}
    assert private(get(other, '/bookmarks')).json()['articles'] == []
    page = private(get(reader, '/bookmarks')).json()
    assert [p['id'] for p in page['articles']] == [pid]
    html = private(reader.get(WEB_TEST_BASE_URL + '/bookmarks', timeout=30))
    doc = PyQuery(html.text)
    assert doc('meta[name="robots"]').attr('content') == 'noindex, nofollow'
    assert len(doc('#bookmarked-articles .article')) == 1
    saved_control = doc('#bookmarked-articles .btn-article-bookmark')
    assert saved_control.attr('aria-pressed') == 'true'
    assert saved_control.attr('disabled') is None
    assert doc('a[href="/bookmarks"]').text() == 'Bookmarks'
    assert dynamodb_table.get_item(Key={'pk': 'POST#' + pid, 'sk': 'META'})['Item'] == {**before_article, 'bookmarks_count': 1, 'bookmarks_revision': 1}
    assert dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'META'})['Item'] == before_user
    assert private(post(reader, path, json={'saved': False})).json() == {'saved': False, 'bookmarks_count': 0}
    assert private(post(reader, path, json={'saved': False})).json() == {'saved': False, 'bookmarks_count': 0}
    assert 'Item' not in dynamodb_table.get_item(Key={'pk': pointer['pk'], 'sk': pointer['sk']})
    assert 'Item' not in dynamodb_table.get_item(Key={
        'pk': pointer['pk'], 'sk': bookmark_order_key(pointer['created_at'], pid)})
    # Owners may bookmark their own published work.
    private(post(author, path, json={'saved': True}))


def test_detail_bookmark_state_is_ready_in_initial_html(library):
    (reader, _), _, _, ids = library
    url = WEB_TEST_BASE_URL + '/articles/' + ids[0]
    def control():
        response = private(reader.get(url, timeout=30))
        return PyQuery(response.text)('#article-controls .btn-article-bookmark')
    initial = control()
    assert initial.attr('aria-pressed') == 'false'
    assert initial.attr('data-bookmark-saved') == 'false'
    assert initial.attr('disabled') is None
    assert initial('span').eq(0).text() == 'Bookmark'
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': True}))
    saved = control()
    assert saved.attr('aria-pressed') == 'true'
    assert saved.attr('data-bookmark-saved') == 'true'
    assert saved.attr('disabled') is None
    assert saved('span').eq(0).text() == 'Bookmarked'
    assert saved('[data-bookmarks-count]').text() == '1'


def test_bookmark_counts_are_atomic_for_multiple_readers_and_retries(library):
    from concurrent.futures import ThreadPoolExecutor
    (reader, _), _, (other, _), ids = library
    path = '/bookmarks/' + ids[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda client: private(post(client, path, json={'saved': True})).json(), [reader, other]))
    assert sorted(r['bookmarks_count'] for r in results) == [1, 2]
    assert private(post(reader, path, json={'saved': True})).json()['bookmarks_count'] == 2
    assert private(post(reader, path, json={'saved': False})).json()['bookmarks_count'] == 1
    assert private(post(reader, path, json={'saved': False})).json()['bookmarks_count'] == 1
    assert private(post(other, path, json={'saved': False})).json()['bookmarks_count'] == 0
    item = dynamodb_table.get_item(Key={'pk': 'POST#' + ids[0], 'sk': 'META'}, ConsistentRead=True)['Item']
    assert item['bookmarks_count'] == 0 and item['bookmarks_revision'] == 4


def test_bookmarks_filter_unavailable_articles_and_scope_cursors(library):
    (reader, uid), (_, owner), (other, _), ids = library
    for index, pid in enumerate(ids):
        private(post(reader, '/bookmarks/' + pid, json={'saved': True}))
        # Make ordering deterministic without sleeps.
        pointer = dynamodb_table.get_item(Key={'pk': 'USER#' + uid, 'sk': 'BOOKMARK#' + pid})['Item']
        dynamodb_table.delete_item(Key={'pk': pointer['pk'], 'sk': bookmark_order_key(pointer['created_at'], pid)})
        dynamodb_table.put_item(Item={'pk': pointer['pk'], 'sk': bookmark_order_key(100 + index, pid)})
        dynamodb_table.update_item(Key={'pk': pointer['pk'], 'sk': pointer['sk']},
                                  UpdateExpression='SET created_at = :time', ExpressionAttributeValues={':time': 100 + index})
    first = private(get(reader, '/bookmarks?limit=1')).json()
    assert first['articles'][0]['id'] == ids[2] and first['offset']
    second = private(get(reader, '/bookmarks', params={'limit': 1, 'offset': first['offset']})).json()
    assert second['articles'][0]['id'] == ids[1]
    private(get(other, '/bookmarks', params={'offset': first['offset']}), 422)
    dynamodb_table.update_item(Key={'pk': 'POST#' + ids[2], 'sk': 'META'},
                              UpdateExpression='SET #status = :status',
                              ExpressionAttributeNames={'#status': 'status'}, ExpressionAttributeValues={':status': 'unpublished'})
    dynamodb_table.delete_item(Key={'pk': 'POST#' + ids[1], 'sk': 'META'})
    assert [p['id'] for p in private(get(reader, '/bookmarks')).json()['articles']] == [ids[0]]
    private(post(reader, '/bookmarks/' + ids[1], json={'saved': False}))
    dynamodb_table.update_item(Key={'pk': 'USER#' + owner, 'sk': 'META'},
                              UpdateExpression='SET deletion_id = :code', ExpressionAttributeValues={':code': 'deleting'})
    assert private(get(reader, '/bookmarks')).json()['articles'] == []


def test_bookmark_validation_guest_signup_and_banned_accounts(library):
    (reader, uid), _, _, ids = library
    guest = get_guest_client()
    private(post(guest, '/bookmarks/' + ids[0], json={'saved': True}), 401)
    private(get(guest, '/bookmarks'), 401)
    html = guest.get(WEB_TEST_BASE_URL + '/articles/' + ids[0], timeout=30)
    assert html.status_code == 200, html.text
    signup = PyQuery(html.text)('a[aria-label="Sign up to bookmark this article"]')
    assert signup and signup.attr('href').startswith('/login?') and 'redirect_url=' in signup.attr('href')
    assert signup('span').eq(0).text() == 'Bookmark'
    assert signup.attr('title') == 'Sign up to bookmark this article'
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': 'true'}), 422)
    private(post(reader, '/bookmarks/status', json={'article_ids': ids * 34}), 422)
    private(post(reader, '/bookmarks/status', json={'article_ids': [12]}), 422)
    private(get(reader, '/bookmarks?offset=not-a-cursor'), 422)
    private(get(reader, '/bookmarks?limit=1000'), 422)
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': True},
                 headers={'Origin': 'https://untrusted.example'}), 403)
    private(post(reader, '/bookmarks/missing-article', json={'saved': True}), 404)
    dynamodb_table.update_item(Key={'pk': 'USER#' + uid, 'sk': 'META'},
                              UpdateExpression='SET #status = :status', ExpressionAttributeNames={'#status': 'status'},
                              ExpressionAttributeValues={':status': 'banned'})
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': True}), 403)


def test_bookmark_transaction_guards_active_accounts_and_publication(monkeypatch):
    from types import SimpleNamespace
    user = SimpleNamespace(id='reader', status='active')
    def read(pk, sk, **kwargs):
        if sk.startswith('BOOKMARK#'):
            return None
        return {'pk': pk, 'user_id': 'owner', 'status': 'published'} if pk == 'POST#example' else {'status': 'active'}
    monkeypatch.setattr(bookmark_service, 'get_dynamodb_item', read)
    write = Mock()
    monkeypatch.setattr(bookmark_service, 'dynamodb_transact_write', write)
    monkeypatch.setattr(bookmark_service, 'get_url', lambda *a, **kw: '/articles/example')
    monkeypatch.setattr(bookmark_service, 'get_user_url', lambda *a, **kw: '/@reader')
    monkeypatch.setattr(bookmark_service, '_notify_bookmark', Mock())
    bookmark_service.set_bookmark(user, 'example', True, None)
    guards = [w['ConditionCheck'] for w in write.call_args.args[0] if 'ConditionCheck' in w]
    assert {g['Key']['pk'] for g in guards} == {'USER#reader', 'USER#owner'}
    assert any('deletion_id' in g['ConditionExpression'] and ':active' in g.get('ExpressionAttributeValues', {}) for g in guards)
    updates = [w['Update'] for w in write.call_args.args[0] if 'Update' in w]
    assert len(updates) == 1 and updates[0]['Key']['pk'] == 'POST#example'
    assert updates[0]['ExpressionAttributeValues'][':published'] == 'published'
    assert 'bookmarks_revision' in updates[0]['ExpressionAttributeNames'].values()


def test_bookmark_notifications_follow_preferences_and_keep_membership_private(library):
    from boto3.dynamodb.conditions import Key
    from shared_utils import utc_now
    from test_utils import patch

    (reader, uid), (author, owner), _, ids = library
    owner_key = {'pk': 'USER#' + owner, 'sk': 'META'}
    dynamodb_table.update_item(Key=owner_key, UpdateExpression='SET last_seen_at = :now',
                              ExpressionAttributeValues={':now': utc_now()})
    def notices():
        return dynamodb_table.query(KeyConditionExpression=Key('pk').eq(owner_key['pk']) &
                                    Key('sk').begins_with('SITE_NOTIFICATION#'), ConsistentRead=True)['Items']

    settings_url = '/users/' + owner + '/edit'
    settings = PyQuery(get(author, settings_url).text)
    assert settings('[name="notify_article_bookmarks"]').is_(':checked')
    private(post(author, '/bookmarks/' + ids[0], json={'saved': True}))
    assert notices() == []  # Self-bookmarks do not notify.
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': True}))
    rows = notices()
    assert len(rows) == 1
    row = rows[0]
    assert row['type'] == 'notify_article_bookmarks'
    assert row['message'] == 'Someone bookmarked your article “Bookmark example 0”'
    assert row['delivery'] == 'web' and row['article_id'] == ids[0]
    assert row['actor_id'] == uid  # Internal reference supports erasure cleanup.
    public = get(author, '/notifications/history').json()['notifications'][0]
    assert public['icon'] == 'bookmark'
    assert 'actor_id' not in public and uid not in str(public)
    assert 'Reader' not in str(public) and '/@reader' not in str(public)
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': True}))
    assert len(notices()) == 1  # An HTTP retry does not duplicate delivery.
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': False}))
    assert len(notices()) == 1  # Removal is private and silent.
    private(post(reader, '/bookmarks/' + ids[0], json={'saved': True}))
    assert len(notices()) == 2  # A later real save has its own revision receipt.
    response = patch(author, '/users/' + owner, json={'notify_article_bookmarks': False})
    assert response.status_code == 200, response.text
    assert not PyQuery(get(author, settings_url).text)('[name="notify_article_bookmarks"]').is_(':checked')
    private(post(reader, '/bookmarks/' + ids[1], json={'saved': True}))
    assert len(notices()) == 2
    assert dynamodb_table.get_item(Key={'pk': 'POST#' + ids[1], 'sk': 'META'})['Item']['bookmarks_count'] == 1


def test_account_erasure_recognizes_both_bookmark_keys():
    from account_deletion_service import _article_id
    assert _article_id({'pk': 'USER#reader', 'sk': 'BOOKMARK#example'}) == 'example'
    assert _article_id({'pk': 'USER#reader', 'sk': bookmark_order_key(123, 'example')}) == 'example'


def test_count_rebuild_counts_only_pointers_and_guards_concurrent_changes():
    from botocore.exceptions import ClientError
    from rebuild_bookmark_counts import rebuild
    table = Mock()
    table.get_item.return_value = {'Item': {'pk': 'POST#example', 'sk': 'META', 'bookmarks_revision': 8}}
    table.scan.return_value = {'Items': [
        {'pk': 'USER#one', 'sk': 'BOOKMARK#example'},
        {'pk': 'USER#one', 'sk': bookmark_order_key(123, 'example')},
        {'pk': 'USER#two', 'sk': 'BOOKMARK#example'},
        {'pk': 'USER#two', 'sk': 'BOOKMARK#other'},
    ]}
    assert list(rebuild(table, ['example'])) == [
        {'article_id': 'example', 'before': 0, 'bookmarks_count': 2, 'result': 'preview'}]
    table.update_item.assert_not_called()
    assert list(rebuild(table, ['example'], apply=True))[0]['result'] == 'applied'
    update = table.update_item.call_args.kwargs
    assert update['UpdateExpression'] == 'SET #count = :count'
    assert update['ExpressionAttributeValues'] == {':count': 2, ':old_bookmarks_revision': 8}
    assert 'attribute_exists(pk)' in update['ConditionExpression']
    assert 'attribute_not_exists(#count)' in update['ConditionExpression']
    assert '#revision = :old_bookmarks_revision' in update['ConditionExpression']
    table.update_item.side_effect = ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem')
    assert list(rebuild(table, ['example'], apply=True))[0]['result'] == 'changed_during_scan'
