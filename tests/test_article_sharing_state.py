"""Confirmed share history changes independently of article content and discovery."""
import logging
import os
from pathlib import Path
import sys
from unittest.mock import Mock
import uuid

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import article_sharing
import shared_utils
from shared_utils import article_from_dynamodb
from notifications import TelegramFormatter

PROMPT_URL = 'https://example.com/@owner/example-article'
POST_URLS = {'telegram': 'https://t.me/example_channel/42',
             'threads': 'https://www.threads.com/@example_creator/post/example'}


@pytest.fixture
def stored_article(monkeypatch):
    from test_utils import dynamodb_table, recreate_dynamodb_table

    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    identifier = 'share-state-' + str(uuid.uuid4())
    item = {
        'pk': 'POST#' + identifier, 'sk': 'META', 'id': identifier, 'user_id': 'example-owner',
        'title': 'Example article', 'post_slug': 'example-article', 'status': 'published',
        'content': 'An article body', 'rating_sk': 3,
        'created_at': 1, 'updated_at': 2, 'published_at': 2, 'likes_count': 3, 'copies_count': 4,
    }
    dynamodb_table.put_item(Item=item)
    yield dynamodb_table, item, article_from_dynamodb(item)
    dynamodb_table.delete_item(Key={'pk': item['pk'], 'sk': item['sk']})


@pytest.mark.parametrize('platform', ['telegram', 'threads'])
def test_successful_shares_and_reshares_persist_without_changing_article_content(stored_article, monkeypatch, platform):
    table, before, article = stored_article
    assert article.telegram_shared_at is None and article.threads_shared_at is None
    times = iter((1000, 2000))
    monkeypatch.setattr(article_sharing, 'utc_now', lambda: next(times))
    result = {'shared': True, 'message_id': 'remote-post', 'access_token': 'must-not-be-logged'}
    log = Mock()
    monkeypatch.setattr(article_sharing, 'logger', log)
    for timestamp in (1000, 2000):
        assert article_sharing.remember_article_share(article, platform, result, article_url=PROMPT_URL, post_url=POST_URLS[platform]) is result
        saved = table.get_item(Key={'pk': before['pk'], 'sk': before['sk']}, ConsistentRead=True)['Item']
        assert saved == {**before, f'{platform}_shared_at': timestamp}
        assert getattr(article_from_dynamodb(saved), f'{platform}_shared_at') == timestamp
        assert log.info.call_count == (1 if timestamp == 1000 else 2)
        context = log.info.call_args.kwargs['extra']['context']
        assert context['article_url'] == PROMPT_URL
        assert context['post_url'] == POST_URLS[platform]
        assert context['article_id'] == article.id and context['owner_id'] == article.owner_id
        assert context['reshare'] == (timestamp == 2000)
        assert 'access_token' not in context
        record = logging.LogRecord('app', logging.INFO, __file__, 1,
                                   log.info.call_args.args[0], log.info.call_args.args[1:], None)
        record.context = context
        notification = TelegramFormatter('%(levelname)s %(message)s').format(record)
        assert PROMPT_URL in notification and POST_URLS[platform] in notification
        assert 'must-not-be-logged' not in notification


@pytest.mark.parametrize('status', ['processing', 'unconfirmed'])
def test_incomplete_shares_do_not_mark_article_as_shared(stored_article, monkeypatch, status):
    _, _, article = stored_article
    write = Mock()
    monkeypatch.setattr(article_sharing, 'dynamodb_transact_write', write)
    result = {'shared': False, 'status': status}
    log = Mock()
    monkeypatch.setattr(article_sharing, 'logger', log)
    assert article_sharing.remember_article_share(article, 'threads', result, article_url=PROMPT_URL) is result
    write.assert_not_called()
    log.info.assert_not_called()
    assert article.threads_shared_at is None


def test_remote_success_remains_success_if_share_history_cannot_be_saved(stored_article, monkeypatch):
    _, _, article = stored_article
    monkeypatch.setattr(article_sharing, 'dynamodb_transact_write', Mock(side_effect=RuntimeError('Unavailable')))
    result = {'shared': True}
    assert article_sharing.remember_article_share(article, 'telegram', result, article_url=PROMPT_URL) is result
    assert article.telegram_shared_at is None


def test_share_history_cannot_recreate_a_deleted_article(stored_article):
    table, before, article = stored_article
    key = {'pk': before['pk'], 'sk': before['sk']}
    table.delete_item(Key=key)
    assert article_sharing.remember_article_share(article, 'telegram', {'shared': True}, article_url=PROMPT_URL)['shared']
    assert 'Item' not in table.get_item(Key=key, ConsistentRead=True)
