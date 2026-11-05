"""Profile activity calendars include the full selected period across query pages."""
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

project_root = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'web-lambda'):
    sys.path.insert(0, str(project_root / directory))

import web_utils


def activity(identifier, month, visible=True):
    return {'id': identifier, 'event_type': 'article.created', 'entity_type': 'article',
            'entity_id': identifier, 'entity_title': identifier, 'profile_visible': visible,
            'created_at': int(datetime(2025, month, 15, tzinfo=timezone.utc).timestamp() * 1000)}


def test_calendar_reads_older_months_after_thousand_activity_page_and_hidden_page(monkeypatch):
    first_cursor = {'pk': 'USER_ACTIVITY#importer', 'sk': 'ACTIVITY#first'}
    second_cursor = {'pk': 'USER_ACTIVITY#importer', 'sk': 'ACTIVITY#second'}
    pages = iter([
        {'Items': [activity(str(index), 11) for index in range(1000)], 'LastEvaluatedKey': first_cursor},
        {'Items': [activity('hidden', 10, visible=False)], 'LastEvaluatedKey': second_cursor},
        {'Items': [activity('october', 10)]},
    ])
    calls = []

    def query(**params):
        calls.append(params)
        return next(pages)

    monkeypatch.setattr(web_utils, 'query_dynamodb_table', query)
    result = web_utils.get_user_activities(SimpleNamespace(id='importer'), year=2025, recent_limit=3)
    assert result['total'] == 1001
    assert result['days'] == {'2025-11-15': 1000, '2025-10-15': 1}
    october = next(month for month in result['months'] if month['key'] == '2025-10')
    assert sum(day['count'] for day in october['days']) == 1
    assert [entry.id for entry in result['recent']] == ['0', '1', '2']
    assert [call['exclusive_start_key'] for call in calls] == [None, first_cursor, second_cursor]
    assert all(call['scan_index_forward'] is False for call in calls)
    bounds = calls[0]['key_condition_expr'].get_expression()['values'][1].get_expression()['values'][1:]
    assert bounds == ('ACTIVITY#1735689600000', 'ACTIVITY#1767225599999#~')


def test_calendar_queries_activity_even_when_saved_user_counters_are_zero(monkeypatch):
    calls = []

    def query(**params):
        calls.append(params)
        return {'Items': [activity('comment', 10)]}

    monkeypatch.setattr(web_utils, 'query_dynamodb_table', query)
    user = SimpleNamespace(id='commenter', published_articles_count=0, article_comments_count=0)
    result = web_utils.get_user_activities(user, year=2025)
    assert len(calls) == 1
    assert result['total'] == 1
