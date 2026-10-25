"""Real DynamoDB coverage for directional engagement totals and share idempotency."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
from unittest.mock import Mock
import uuid

import pytest
from botocore.exceptions import ClientError

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import api_utils
import engagement_service as service
import shared_utils
from article_dtos import ArticleCommentDTO, UpdateArticleCommentImpressionDTO, UpdateArticleImpressionDTO
from shared_utils import article_from_dynamodb, user_from_dynamodb
from user_dtos import UpdateUserImpressionDTO
from web import HTTPException


@pytest.fixture
def records(monkeypatch):
    from test_utils import dynamodb_table, recreate_dynamodb_table
    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    prefix = 'engagement-' + uuid.uuid4().hex
    users = []
    for name in ('owner', 'actor'):
        identifier = prefix + '-' + name
        item = {'pk': 'USER#' + identifier, 'sk': 'META', 'id': identifier, 'name': name,
                'permissions': ['root'], 'created_at': 1, 'updated_at': 2}
        dynamodb_table.put_item(Item=item)
        users.append(user_from_dynamodb(item))
    item = {'pk': 'POST#' + prefix, 'sk': 'META', 'id': prefix, 'user_id': users[0].id,
            'title': 'Example article', 'post_slug': 'example-article', 'status': 'published',
            'content': 'An article body', 'rating_sk': 0,
            'created_at': 1, 'updated_at': 2, 'published_at': 1}
    dynamodb_table.put_item(Item=item)
    monkeypatch.setattr(api_utils, '_send_account_notification', Mock())
    monkeypatch.setattr(api_utils, 'get_article_comment_url', lambda *args, **kwargs: 'https://catalog.example/comment')
    monkeypatch.setattr(service, '_notify_share', Mock())
    yield dynamodb_table, users[0], users[1], article_from_dynamodb(item)
    from boto3.dynamodb.conditions import Key
    for partition in ('USER#' + users[0].id, 'USER#' + users[1].id,
                      'POST#' + prefix, 'POST_SHARE#' + prefix):
        for record in dynamodb_table.query(KeyConditionExpression=Key('pk').eq(partition))['Items']:
            dynamodb_table.delete_item(Key={'pk': record['pk'], 'sk': record['sk']})


def read(table, kind, identifier):
    return table.get_item(Key={'pk': kind + '#' + identifier, 'sk': 'META'}, ConsistentRead=True)['Item']


def test_article_reaction_switch_and_removal_update_both_users_atomically(records):
    table, owner, actor, article = records
    for action, likes, dislikes in [('like', 1, 0), ('dislike', 0, 1), ('dislike', 0, 0)]:
        api_utils.update_article_impression(article, UpdateArticleImpressionDTO(action), actor, None)
        for user, direction in ((owner, 'received'), (actor, 'given')):
            saved = read(table, 'USER', user.id)
            assert saved.get(f'article_likes_{direction}_count', 0) == likes
            assert saved.get(f'article_dislikes_{direction}_count', 0) == dislikes
        saved_article = read(table, 'POST', article.id)
        assert saved_article['likes_count'] == likes
        assert saved_article.get('dislikes_count', 0) == dislikes
    assert api_utils._send_account_notification.call_count == 1


def test_blocks_switch_and_removal_track_both_directions(records):
    table, owner, actor, _ = records
    for action, blocking, following in [('block', 1, 0), ('follow', 0, 1), ('block', 1, 0), ('block', 0, 0)]:
        api_utils.update_user_impression(owner, UpdateUserImpressionDTO(action), actor, None)
        assert read(table, 'USER', owner.id).get('blockers_count', 0) == blocking
        assert read(table, 'USER', actor.id).get('blocking_count', 0) == blocking
        assert read(table, 'USER', owner.id).get('followers_count', 0) == following
        assert read(table, 'USER', actor.id).get('following_count', 0) == following


def test_comments_count_both_directions_and_allow_comments_on_own_article(records):
    table, owner, actor, article = records
    api_utils.create_article_comment(article, ArticleCommentDTO('A useful comment.'), actor, None)
    api_utils.create_article_comment(article, ArticleCommentDTO('An owner response.'), owner, None)
    assert read(table, 'USER', actor.id)['article_comments_given_count'] == 1
    assert read(table, 'USER', owner.id)['article_comments_received_count'] == 2
    assert read(table, 'USER', owner.id)['article_comments_given_count'] == 1
    assert api_utils._send_account_notification.call_count == 1


def test_comment_reactions_toggle_both_directions(records):
    table, owner, actor, article = records
    comment = api_utils.create_article_comment(article, ArticleCommentDTO('A useful comment.'), actor, None)
    for action, likes, dislikes in [('like', 1, 0), ('dislike', 0, 1), ('dislike', 0, 0)]:
        api_utils.update_article_comment_impression(article, comment, UpdateArticleCommentImpressionDTO(action), owner, None)
        for user, direction in ((actor, 'received'), (owner, 'given')):
            saved = read(table, 'USER', user.id)
            assert saved.get(f'comment_likes_{direction}_count', 0) == likes
            assert saved.get(f'comment_dislikes_{direction}_count', 0) == dislikes



def test_sharing_counts_owner_guest_and_other_user_with_idempotency(records):
    table, owner, actor, article = records
    for user in (owner, actor, None):
        assert service.record_article_share(article, user, 'email_share_click', 'event', article_url='https://catalog.example/article')
        assert not service.record_article_share(article, user, 'email_share_click', 'event', article_url='https://catalog.example/article')
    assert read(table, 'POST', article.id)['shares_counts'] == {'email_share_click': 3}
    assert read(table, 'USER', owner.id)['article_shares_received_counts'] == {'email_share_click': 3}
    for user in (owner, actor):
        assert read(table, 'USER', user.id)['article_shares_given_counts'] == {'email_share_click': 1}
    assert read(table, 'POST', article.id)['updated_at'] == 2
    assert service._notify_share.call_count == 2


def test_replayed_concurrent_share_is_counted_once(records):
    table, owner, actor, article = records
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: service.record_article_share(
            article, actor, 'threads_publish', 'remote-post', article_url='https://catalog.example/article'), range(4)))
    assert sum(results) == 1
    assert read(table, 'POST', article.id)['shares_counts'] == {'threads_publish': 1}
    assert read(table, 'USER', owner.id)['article_shares_received_counts'] == {'threads_publish': 1}


def test_share_notification_failure_preserves_committed_event(records):
    table, owner, actor, article = records
    service._notify_share.side_effect = RuntimeError('Email unavailable')
    assert service.record_article_share(article, actor, 'telegram_publish', 'remote-post',
                                       article_url='https://catalog.example/article')
    assert read(table, 'USER', owner.id)['article_shares_received_counts'] == {'telegram_publish': 1}


def test_comment_recipient_lookup_failure_preserves_committed_reaction(records, monkeypatch):
    table, owner, actor, article = records
    comment = api_utils.create_article_comment(article, ArticleCommentDTO('A useful comment.'), actor, None)
    monkeypatch.setattr(api_utils, 'get_user', Mock(side_effect=RuntimeError('Lookup unavailable')))
    api_utils.update_article_comment_impression(article, comment, UpdateArticleCommentImpressionDTO('like'), owner, None)
    assert read(table, 'USER', actor.id)['comment_likes_received_count'] == 1


def test_removing_unbackfilled_legacy_reactions_does_not_make_totals_negative(records):
    table, owner, actor, article = records
    for user in (owner, actor):
        field = 'article_likes_received_count' if user.id == owner.id else 'article_likes_given_count'
        table.update_item(Key={'pk': 'USER#' + user.id, 'sk': 'META'},
                          UpdateExpression='SET #counter = :zero',
                          ExpressionAttributeNames={'#counter': field}, ExpressionAttributeValues={':zero': 0})
    table.put_item(Item={'pk': 'POST#' + article.id, 'sk': 'IMP#' + actor.id,
                        'post_id': article.id, 'user_id': actor.id, 'action': 'like', 'created_at': 1})
    table.update_item(Key={'pk': 'POST#' + article.id, 'sk': 'META'},
                      UpdateExpression='SET likes_count = :one', ExpressionAttributeValues={':one': 1})
    api_utils.update_article_impression(article, UpdateArticleImpressionDTO('like'), actor, None)
    assert read(table, 'USER', owner.id)['article_likes_received_count'] == 0
    assert read(table, 'USER', actor.id)['article_likes_given_count'] == 0


def test_stale_article_reaction_cannot_apply_twice(records, monkeypatch):
    table, owner, actor, article = records
    api_utils.update_article_impression(article, UpdateArticleImpressionDTO('like'), actor, None)
    stale = shared_utils.find_article_impression(article, actor)
    monkeypatch.setattr(api_utils, 'find_article_impression', lambda *args: stale)
    api_utils.update_article_impression(article, UpdateArticleImpressionDTO('dislike'), actor, None)
    before = read(table, 'POST', article.id)
    with pytest.raises(HTTPException) as error:
        api_utils.update_article_impression(article, UpdateArticleImpressionDTO('dislike'), actor, None)
    assert error.value.status_code == 409
    assert read(table, 'POST', article.id) == before
    assert read(table, 'USER', owner.id)['article_dislikes_received_count'] == 1


def test_existing_self_reaction_policy_preserves_both_counter_directions(records):
    table, owner, _, article = records
    for action, count in [('like', 1), ('like', 0)]:
        api_utils.update_article_impression(article, UpdateArticleImpressionDTO(action), owner, None)
        saved = read(table, 'USER', owner.id)
        assert saved['article_likes_given_count'] == saved['article_likes_received_count'] == count
    api_utils._send_account_notification.assert_not_called()


def test_backfill_dry_run_apply_and_rerun(records):
    from rebuild_engagement_statistics import rebuild
    table, owner, actor, article = records
    api_utils.update_article_impression(article, UpdateArticleImpressionDTO('like'), actor, None)
    api_utils.create_article_comment(article, ArticleCommentDTO('Historical comment'), actor, None)
    key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    table.update_item(Key=key, UpdateExpression='SET article_likes_given_count = :bad',
                      ExpressionAttributeValues={':bad': 999})
    before = read(table, 'USER', actor.id)
    report = rebuild(actor.id)
    assert report['output']['applied'] is False
    assert report['output']['changes']['article_likes_given_count'] == 1
    assert report['output']['changes']['article_comments_given_count'] == 1
    assert read(table, 'USER', actor.id) == before
    assert rebuild(actor.id, apply=True)['output']['applied'] is True
    assert read(table, 'USER', actor.id)['article_likes_given_count'] == 1
    assert rebuild(actor.id, apply=True)['output']['action'] == 'skip'


def test_backfill_rejects_concurrent_activity(records, monkeypatch):
    from rebuild_engagement_statistics import rebuild
    table, _, actor, _ = records
    key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    table.update_item(Key=key, UpdateExpression='SET article_likes_given_count = :bad',
                      ExpressionAttributeValues={':bad': 999})
    original = service._map_update
    def racing_update(*args, **kwargs):
        table.update_item(Key=key, UpdateExpression='ADD engagement_revision :one',
                          ExpressionAttributeValues={':one': 1})
        return original(*args, **kwargs)
    monkeypatch.setattr(service, '_map_update', racing_update)
    with pytest.raises((shared_utils.DynamoDBTransactionError, ClientError)):
        rebuild(actor.id, apply=True)
    assert read(table, 'USER', actor.id)['article_likes_given_count'] == 999
