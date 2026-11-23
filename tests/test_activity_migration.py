"""Historical counter conversion is resumable and preserves concurrent live writes."""
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'shared'))

import migrate_activity_counters as migration
from activity_counters import activity_counts


@pytest.fixture
def history():
    from test_utils import dynamodb_table as table, recreate_dynamodb_table
    from boto3.dynamodb.conditions import Key

    try:
        table.load()
    except table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    uid = 'activity-' + uuid4().hex
    user_key = {'pk': 'USER#' + uid, 'sk': 'META'}
    table.put_item(Item={**user_key, 'id': uid, 'created_at': 1, 'updated_at': 2})

    def event(number, *, visible=True, kind='article.created', timestamp=1735689600000):
        item = {'pk': 'USER_ACTIVITY#' + uid, 'sk': f'ACTIVITY#{timestamp}#{number:06d}',
                'event_type': kind, 'created_at': timestamp, 'profile_visible': visible,
                'entity_title': 'Historical private title', 'entity_id': 'old-id'}
        table.put_item(Item=item)
        return item

    yield table, uid, user_key, event
    for item in table.query(KeyConditionExpression=Key('pk').eq('USER_ACTIVITY#' + uid))['Items']:
        table.delete_item(Key={'pk': item['pk'], 'sk': item['sk']})
    table.delete_item(Key=user_key)


def day(table, uid, date='2025-01-01'):
    return table.get_item(Key={'pk': 'USER_ACTIVITY#' + uid, 'sk': 'DAY#' + date}, ConsistentRead=True).get('Item', {})


def test_dry_run_validates_without_writing(history):
    table, uid, key, event = history
    event(1)
    event(2, visible=False)
    result = migration.migrate_user(table, uid)
    assert result == {'user_id': uid, 'legacy_rows': 2, 'visible_events': 1, 'hidden_rows': 1,
                      'days': 1, 'applied': False}
    assert not day(table, uid)
    assert 'activity_v' not in table.get_item(Key=key)['Item']
    assert all('m' not in item for item in migration._rows(table, uid, 'ACTIVITY#'))


def test_paginated_history_is_verified_and_pruned_to_only_typed_counters(history):
    table, uid, key, event = history
    for index in range(205):
        event(index, kind='comment.created' if index % 2 else 'article.created')
    event(206, visible=False)
    event(207, timestamp=1735689599999)  # Previous UTC day/year.
    table.update_item(Key={'pk': 'USER_ACTIVITY#' + uid, 'sk': 'DAY#2025-01-01'},
                      UpdateExpression='ADD #kind :n', ExpressionAttributeNames={'#kind': 'article.created'},
                      ExpressionAttributeValues={':n': 7})
    result = migration.migrate_user(table, uid, apply=True, prune_legacy=True)
    assert result['details_removed'] is True
    assert list(migration._rows(table, uid, 'ACTIVITY#')) == []
    assert day(table, uid) == {'pk': 'USER_ACTIVITY#' + uid, 'sk': 'DAY#2025-01-01',
                               'article.created': 110, 'comment.created': 102}
    assert day(table, uid, '2024-12-31')['article.created'] == 1
    assert table.get_item(Key=key)['Item']['updated_at'] == 2
    assert table.get_item(Key=key)['Item']['activity_v'] == 1
    migration.migrate_user(table, uid, apply=True, prune_legacy=True)
    assert activity_counts(day(table, uid)) == {'article.created': 110, 'comment.created': 102}


def test_resumes_staging_then_partial_pruning_without_recounting(history):
    table, uid, key, event = history
    first, second = event(1), event(2)
    migration._stage(table, uid, [first])  # Process interrupted before activation.
    migration._stage(table, uid, [first])  # Stale concurrent migration retries the same row.
    table.update_item(Key={'pk': 'USER_ACTIVITY#' + uid, 'sk': 'DAY#2025-01-01'},
                      UpdateExpression='ADD #kind :n', ExpressionAttributeNames={'#kind': 'article.created'},
                      ExpressionAttributeValues={':n': 3})
    migration.migrate_user(table, uid, apply=True)
    assert activity_counts(day(table, uid), include_legacy=True) == {'article.created': 5}
    marked = table.get_item(Key={'pk': first['pk'], 'sk': first['sk']})['Item']
    migration._prune(table, uid, [marked])  # Process interrupted during pruning.
    migration.migrate_user(table, uid, apply=True, prune_legacy=True)
    assert activity_counts(day(table, uid)) == {'article.created': 5}
    assert list(migration._rows(table, uid, 'ACTIVITY#')) == []


def test_counter_merge_preserves_a_live_increment_after_its_read(history, monkeypatch):
    table, uid, key, event = history
    event(1)
    migration.migrate_user(table, uid, apply=True)
    transact = table.meta.client.transact_write_items
    injected = False

    def concurrent(**kwargs):
        nonlocal injected
        if not injected:
            injected = True
            table.update_item(Key={'pk': 'USER_ACTIVITY#' + uid, 'sk': 'DAY#2025-01-01'},
                              UpdateExpression='ADD #kind :n', ExpressionAttributeNames={'#kind': 'article.created'},
                              ExpressionAttributeValues={':n': 1})
        return transact(**kwargs)

    monkeypatch.setattr(table.meta.client, 'transact_write_items', concurrent)
    migration._merge(table, uid, {'pk': 'USER_ACTIVITY#' + uid, 'sk': 'DAY#2025-01-01'})
    assert activity_counts(day(table, uid)) == {'article.created': 2}
    assert 'legacy.article.created' not in day(table, uid)


def test_mismatch_prevents_activation_and_retains_details(history):
    table, uid, key, event = history
    event(1)
    table.put_item(Item={'pk': 'USER_ACTIVITY#' + uid, 'sk': 'DAY#2025-01-01', 'legacy.article.created': 3})
    with pytest.raises(ValueError, match='do not match'):
        migration.migrate_user(table, uid, apply=True, prune_legacy=True)
    assert len(list(migration._rows(table, uid, 'ACTIVITY#'))) == 1
    assert 'activity_v' not in table.get_item(Key=key)['Item']


def test_unknown_visible_type_retains_details(history):
    table, uid, key, event = history
    event(1, kind='unsupported')
    with pytest.raises(ValueError, match='unsupported visible'):
        migration.migrate_user(table, uid, apply=True, prune_legacy=True)
    assert len(list(migration._rows(table, uid, 'ACTIVITY#'))) == 1
    assert not day(table, uid)


def test_erasure_during_staging_cannot_recreate_activity(history, monkeypatch):
    table, uid, key, event = history
    item = event(1)
    transact = table.meta.client.transact_write_items

    def erased(**kwargs):
        table.delete_item(Key=key)
        return transact(**kwargs)

    monkeypatch.setattr(table.meta.client, 'transact_write_items', erased)
    with pytest.raises(ValueError, match='missing'):
        migration._stage(table, uid, [item])
    assert not day(table, uid)
    assert 'm' not in table.get_item(Key={'pk': item['pk'], 'sk': item['sk']})['Item']
