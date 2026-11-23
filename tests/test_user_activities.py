"""Activity calendars read compact UTC-day counters, with safe legacy fallback."""
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'web-lambda', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import web_utils
from activity_counters import activity_day
from activity_service import add_activity_counter


def test_ready_calendar_reads_thousands_of_events_in_one_query(monkeypatch):
    calls = []

    def query(**params):
        calls.append(params)
        return {'Items': [{'sk': 'DAY#2025-10-15', 'article.created': 13333, 'comment.created': 29},
                          {'sk': 'DAY#2025-11-01', 'legacy.article.created': 3}]}

    monkeypatch.setattr(web_utils, 'query_dynamodb_table', query)
    result = web_utils.get_user_activities(SimpleNamespace(id='importer', activity_version=1), year=2025)
    assert result['total'] == 13365
    assert result['days'] == {'2025-10-15': 13362, '2025-11-01': 3}
    october = next(month for month in result['months'] if month['key'] == '2025-10')
    assert sum(day['count'] for day in october['days']) == 13362
    assert 'recent' not in result
    assert len(calls) == 1
    bounds = calls[0]['key_condition_expr'].get_expression()['values'][1].get_expression()['values'][1:]
    assert bounds == ('DAY#2025-01-01', 'DAY#2025-12-31')
    assert calls[0]['consistent_read'] is True


def test_calendar_paginates_daily_rows_even_with_zero_current_resource_counts(monkeypatch):
    cursor = {'pk': 'USER_ACTIVITY#importer', 'sk': 'DAY#2025-10-15'}
    pages = iter([{'Items': [{'sk': 'DAY#2025-10-15', 'article.created': 1}], 'LastEvaluatedKey': cursor},
                  {'Items': [{'sk': 'DAY#2025-11-01', 'comment.created': 2}]}])
    calls = []

    def query(**params):
        calls.append(params)
        return next(pages)

    monkeypatch.setattr(web_utils, 'query_dynamodb_table', query)
    user = SimpleNamespace(id='importer', activity_version=1, published_articles_count=0, article_comments_count=0)
    assert web_utils.get_user_activities(user, year=2025)['total'] == 3
    assert [call['exclusive_start_key'] for call in calls] == [None, cursor]


def test_legacy_calendar_reads_every_page_without_double_counting_staged_history(monkeypatch):
    timestamp = int(datetime(2025, 10, 16, 2, tzinfo=timezone.utc).timestamp() * 1000)
    event = {'created_at': timestamp, 'profile_visible': True, 'event_type': 'article.created'}
    cursor = {'pk': 'USER_ACTIVITY#importer', 'sk': 'ACTIVITY#last'}
    pages = iter([
        {'Items': [{'sk': 'DAY#2025-10-16', 'article.created': 5, 'legacy.article.created': 1001}]},
        {'Items': [event] * 1000, 'LastEvaluatedKey': cursor},
        {'Items': [{**event, 'profile_visible': False}], 'LastEvaluatedKey': cursor},
        {'Items': [event]},
    ])
    calls = []

    def query(**params):
        calls.append(params)
        return next(pages)

    monkeypatch.setattr(web_utils, 'query_dynamodb_table', query)
    monkeypatch.setattr(web_utils, 'get_dynamodb_item', lambda *args, **kwargs: {})
    result = web_utils.get_user_activities(SimpleNamespace(id='importer'), year=2025)
    assert result['days'] == {'2025-10-16': 1006}
    assert [call['exclusive_start_key'] for call in calls] == [None, None, cursor, cursor]
    assert all(call['projection_expr'] == 'event_type, created_at, profile_visible' for call in calls[1:])
    bounds = calls[1]['key_condition_expr'].get_expression()['values'][1].get_expression()['values'][1:]
    assert bounds == ('ACTIVITY#1735689600000', 'ACTIVITY#1767225599999#~')


def test_migration_activation_during_calendar_read_reloads_verified_counters(monkeypatch):
    pages = iter([{'Items': [{'sk': 'DAY#2025-10-15', 'article.created': 5}]},
                  {'Items': []},  # Legacy details were pruned while this request was in flight.
                  {'Items': [{'sk': 'DAY#2025-10-15', 'article.created': 5, 'legacy.article.created': 1001}]}])
    monkeypatch.setattr(web_utils, 'query_dynamodb_table', lambda **kwargs: next(pages))
    monkeypatch.setattr(web_utils, 'get_dynamodb_item', lambda *args, **kwargs: {'activity_v': 1})
    result = web_utils.get_user_activities(SimpleNamespace(id='importer'), year=2025)
    assert result['days'] == {'2025-10-15': 1006}


@pytest.mark.parametrize(('utc', 'day'), [
    ('2024-12-31T23:59:59.999+00:00', '2024-12-31'),
    ('2025-01-01T00:00:00+00:00', '2025-01-01'),
    ('2025-01-01T05:59:59+00:00', '2025-01-01'),
    ('2025-06-30T23:59:59.999+00:00', '2025-06-30'),
    ('2025-07-01T00:00:00+00:00', '2025-07-01'),
    ('2025-07-01T04:59:59+00:00', '2025-07-01'),
    ('2024-02-29T00:00:00+00:00', '2024-02-29'),
    ('2024-12-31T19:00:00-05:00', '2025-01-01'),
    ('2025-01-01T02:00:00+03:00', '2024-12-31'),
])
def test_activity_writes_only_typed_utc_day_counter(utc, day):
    timestamp = int(datetime.fromisoformat(utc).timestamp() * 1000)
    assert activity_day(timestamp) == day
    writes = []
    add_activity_counter(writes, 'creator', 'article.created', timestamp)
    assert len(writes) == 1
    update = writes[0]['Update']
    assert update['Key'] == {'pk': 'USER_ACTIVITY#creator', 'sk': 'DAY#' + day}
    assert update['UpdateExpression'] == 'ADD #activity :one'
    assert update['ExpressionAttributeNames'] == {'#activity': 'article.created'}
    assert update['ExpressionAttributeValues'] == {':one': 1}
    assert set(update) == {'TableName', 'Key', 'UpdateExpression', 'ExpressionAttributeNames', 'ExpressionAttributeValues'}


def test_unsupported_activity_does_not_append_a_write():
    writes = []
    with pytest.raises(ValueError):
        add_activity_counter(writes, 'creator', 'unknown', 1)
    assert writes == []
