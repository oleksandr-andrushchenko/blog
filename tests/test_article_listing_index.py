"""Ordered listings must read a page, preserve cursors, and track mutations."""
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = Path(os.getenv('PROJECT_ROOT', Path(__file__).parents[1]))
sys.path.insert(0, str(project_root / 'shared'))

import shared_utils
from article_listing_index import TAG_RATING_INDEX, tag_listing_fields, tag_rating_key
from query_dtos import ArticleListingQueryDTO


def candidate(article_id, rating, **overrides):
    return SimpleNamespace(id=article_id, rating=rating, created_at=123, tags=['woman'],
                           status='published', category='photography', models=['example-model'],
                           offset=None, **overrides)


def relation(article):
    return {'pk': 'POST_TAG_COMBO#woman', 'sk': f'POST#{article.created_at}#{article.id}',
            **tag_listing_fields(article)}


def test_popular_listing_reads_one_page_from_large_tag(monkeypatch):
    articles = [candidate(f'item-{i:05}', i // 2 - 3000) for i in range(5000)]
    by_id = {p.id: p for p in articles}
    ordered = sorted((relation(p) for p in articles), key=lambda r: r['tag_rating_sk'], reverse=True)
    queries, loads = [], []

    def query(**params):
        queries.append(params.copy())
        start = 0
        if params.get('ExclusiveStartKey'):
            start = next(i + 1 for i, r in enumerate(ordered)
                         if r['sk'] == params['ExclusiveStartKey']['sk'])
        items = ordered[start:start + params['Limit']]
        last = {k: items[-1][k] for k in ('pk', 'sk', 'tag_rating_sk')}
        return {'Items': items, 'LastEvaluatedKey': last}

    def load(ids):
        ids = list(ids)
        loads.append(ids)
        return [by_id[i] for i in ids]

    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: SimpleNamespace(query=query))
    monkeypatch.setattr(shared_utils, 'get_articles_by_ids', load)
    monkeypatch.setattr(shared_utils, 'tag_listing_index_ready', lambda: True)
    first = shared_utils.get_articles(ArticleListingQueryDTO(type='popular', tags=['woman']))
    assert [p.id for p in first] == [r['post_id'] for r in ordered[:40]]
    assert len(queries) == 1 and len(loads[0]) == 41
    assert queries[0]['IndexName'] == TAG_RATING_INDEX
    second = shared_utils.get_articles(ArticleListingQueryDTO(
        type='popular', tags=['woman'], offset=first[-1].offset))
    assert [p.id for p in second] == [r['post_id'] for r in ordered[40:80]]
    assert len(queries) == 2 and len(loads[1]) == 41
    assert not set(p.id for p in first).intersection(p.id for p in second)


@pytest.mark.parametrize('sort', ['latest', 'popular'])
def test_combined_filters_use_tag_partition_and_continue_empty_pages(monkeypatch, sort):
    from boto3.dynamodb.conditions import ConditionExpressionBuilder

    good = candidate('good', 3)
    stale = candidate('stale', 2)
    stale.status = 'unpublished'
    next_key = {'pk': 'POST_TAG_COMBO#woman', 'sk': 'POST#123#empty'}
    if sort == 'popular':
        next_key['tag_rating_sk'] = tag_rating_key(4, 'empty')
    pages = iter([{'Items': [], 'LastEvaluatedKey': next_key},
                  {'Items': [relation(stale), relation(good)]}])
    queries = []

    def query(**params):
        queries.append(params.copy())
        return next(pages)

    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: SimpleNamespace(query=query))
    def load(ids):
        ids = set(ids)
        return [p for p in (stale, good) if p.id in ids]

    monkeypatch.setattr(shared_utils, 'get_articles_by_ids', load)
    monkeypatch.setattr(shared_utils, 'tag_listing_index_ready', lambda: True)
    result = shared_utils.get_articles(ArticleListingQueryDTO(
        type=sort, tags=['woman'], category='photography', limit=2))
    assert [p.id for p in result] == ['good']
    assert result[-1].offset is None
    assert len(queries) == 2 and queries[1]['ExclusiveStartKey'] == next_key
    condition = ConditionExpressionBuilder().build_expression(queries[0]['FilterExpression'])
    assert set(condition.attribute_value_placeholders.values()) == {'photography'}


@pytest.mark.parametrize('change', [{'tags': ['other']}, {'status': 'unpublished'}, {'type': 'latest'}])
def test_popular_tag_cursor_rejects_wrong_scope(change):
    article = candidate('example', -123)
    cursor = {k: relation(article)[k] for k in ('pk', 'sk', 'tag_rating_sk')}
    params = dict(type='popular', tags=['woman'], offset=shared_utils.encode_offset(cursor))
    assert ArticleListingQueryDTO(**params)
    with pytest.raises(ValueError):
        ArticleListingQueryDTO(**(params | change))


def test_tag_rating_key_preserves_negative_and_tied_order():
    pairs = [(-100, 'a'), (-1, 'z'), (0, 'a'), (0, 'b'), (100, 'a')]
    assert sorted(reversed(pairs), key=lambda p: tag_rating_key(*p)) == pairs


def test_rating_mutation_updates_relations_once_and_guards_snapshot(monkeypatch):
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: 'app_test')
    article = candidate('example', -123)
    writes = []
    shared_utils.add_put_tag_combos_transact(writes, article)
    shared_utils.add_dynamodb_article_update_transact(writes, article, deltas={'rating_sk': 10})
    puts = [w['Put']['Item'] for w in writes if 'Put' in w]
    assert len(puts) == 1
    assert puts[0]['tag_rating_sk'] == tag_rating_key(-113, article.id)
    update = next(w['Update'] for w in writes if 'Update' in w)
    assert update['ExpressionAttributeValues'][':listing_rating_sk'] == -123
    assert update['ExpressionAttributeValues'][':listing_status'] == 'published'
    assert '#listing_tags = :listing_tags' in update['ConditionExpression']


def test_incomplete_metadata_batch_does_not_advance_cursor(monkeypatch):
    keys = [{'pk': 'POST#example', 'sk': 'META'}]
    client = SimpleNamespace(batch_get_item=lambda **kw: {'UnprocessedKeys': {'app_test': {'Keys': keys}}})
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table',
                        lambda: SimpleNamespace(name='app_test', meta=SimpleNamespace(client=client)))
    monkeypatch.setattr(shared_utils.time, 'sleep', lambda *a: None)
    with pytest.raises(RuntimeError, match='Unable to read'):
        shared_utils.get_articles_by_ids(['example'])


def test_backfill_dry_run_preserves_article_and_does_not_write(monkeypatch):
    from backfill_tag_listing_index import backfill_article

    item = {'id': 'example', 'user_id': 'owner', 'title': 'Example', 'post_slug': 'example',
            'status': 'published', 'tags': ['woman'], 'rating_sk': 123, 'created_at': 123,
            'content': '<p>Write an example.</p>'}
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: 'app_test')
    monkeypatch.setattr(shared_utils, 'dynamodb_transact_write', lambda *a: pytest.fail('Dry-run write'))
    table = SimpleNamespace(name='app_test', get_item=lambda **kw: {'Item': item})
    result = backfill_article(table, 'example', apply=False)
    assert result == {('POST_TAG_COMBO#woman', 'POST#123#example', tag_rating_key(123, 'example'))}
    assert 'tag_rating_sk' not in item


def test_activation_refuses_partial_index(monkeypatch):
    from backfill_tag_listing_index import activate

    monkeypatch.setattr(shared_utils, 'dynamodb_transact_write', lambda *a: pytest.fail('Premature activation'))
    client = SimpleNamespace(describe_table=lambda **kw: {'Table': {'GlobalSecondaryIndexes': [
        {'IndexName': TAG_RATING_INDEX, 'IndexStatus': 'ACTIVE'}]}})
    table = SimpleNamespace(name='app_test', meta=SimpleNamespace(client=client), scan=lambda **kw: {'Items': []})
    with pytest.raises(RuntimeError, match='verification incomplete'):
        activate(table, {('POST_TAG_COMBO#woman', 'POST#123#example', tag_rating_key(123, 'example'))})


def test_completed_backfill_rerun_skips_writes(monkeypatch):
    from backfill_tag_listing_index import backfill_article

    item = {'pk': 'POST#example', 'sk': 'META', 'id': 'example', 'user_id': 'owner',
            'title': 'Example', 'post_slug': 'example', 'content': '<p>Example</p>',
            'status': 'published', 'tags': ['woman'], 'rating_sk': 123, 'created_at': 123}
    article = shared_utils.article_from_dynamodb(item)
    member = {'pk': 'POST_TAG_COMBO#woman', 'sk': 'POST#123#example', **tag_listing_fields(article)}
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: 'app_test')
    monkeypatch.setattr(shared_utils, 'dynamodb_transact_write', lambda *args: pytest.fail('Completed item rewritten'))
    table = SimpleNamespace(name='app_test', get_item=lambda **kwargs: {
        'Item': item if kwargs['Key']['pk'] == 'POST#example' else member})
    assert backfill_article(table, 'example', apply=True) == {
        (member['pk'], member['sk'], member['tag_rating_sk'])}


def test_real_index_paginates_and_rating_writes_update_order_without_content_changes(monkeypatch):
    from test_utils import dynamodb_table
    from article_listing_index import TAG_LISTING_READY_KEY

    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    prefix = 'rank-' + uuid.uuid4().hex
    keys = []
    saved_marker = dynamodb_table.get_item(Key=dict(zip(('pk', 'sk'), TAG_LISTING_READY_KEY))).get('Item')
    try:
        articles = []
        for number in range(6):
            item = {'pk': 'POST#' + prefix + str(number), 'sk': 'META', 'id': prefix + str(number),
                    'user_id': prefix, 'post_slug': prefix + str(number), 'title': 'Example',
                    'content': '<p>Article</p>', 'status': 'published', 'tags': [prefix],
                    'rating_sk': number // 2 - 2, 'created_at': 123, 'updated_at': 124}
            dynamodb_table.put_item(Item=item)
            keys.append({'pk': item['pk'], 'sk': 'META'})
            article = shared_utils.article_from_dynamodb(item)
            articles.append(article)
            writes = []
            shared_utils.add_put_tag_combos_transact(writes, article)
            shared_utils.dynamodb_transact_write(writes)
            keys.extend({'pk': write['Put']['Item']['pk'], 'sk': write['Put']['Item']['sk']} for write in writes)
        dynamodb_table.put_item(Item={**dict(zip(('pk', 'sk'), TAG_LISTING_READY_KEY)), 'version': 1})
        first = shared_utils.get_articles(ArticleListingQueryDTO(type='popular', tags=[prefix], limit=2))
        second = shared_utils.get_articles(ArticleListingQueryDTO(type='popular', tags=[prefix], limit=2,
                                                                offset=first[-1].offset))
        assert [article.id for article in first + second] == [article.id for article in reversed(articles[2:])]
        writes = []
        shared_utils.add_dynamodb_article_update_transact(writes, articles[0], deltas={'rating_sk': 10}, add_updated_at=False)
        shared_utils.dynamodb_transact_write(writes)
        reranked = shared_utils.get_articles(ArticleListingQueryDTO(type='popular', tags=[prefix], limit=2))
        assert reranked[0].id == articles[0].id
        record = dynamodb_table.get_item(Key=keys[0])['Item']
        assert record['updated_at'] == 124 and record['content'] == '<p>Article</p>'
    finally:
        for key in keys:
            dynamodb_table.delete_item(Key=key)
        if saved_marker:
            dynamodb_table.put_item(Item=saved_marker)
        else:
            dynamodb_table.delete_item(Key=dict(zip(('pk', 'sk'), TAG_LISTING_READY_KEY)))
