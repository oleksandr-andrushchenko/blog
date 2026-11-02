"""Profile propagation stays bounded, resumable, and safe across concurrent edits."""
from copy import deepcopy
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'shared'))
sys.path.insert(0, str(ROOT / 'api-lambda'))

import api_utils
import background_handler
import profile_sync_service as sync
from query_dtos import ArticleStatus
from shared_utils import DynamoDBTransactionError, user_from_dynamodb
from user_dtos import UpdateUserDTO


@pytest.fixture
def propagation(monkeypatch):
    profile = {'id': 'creator', 'name': 'New name', 'username': 'new-slug', 'avatar_filename': 'new.webp'}
    state = {'version': 'initial', 'phase': 0}
    writes = []
    queries = []
    pages = {}

    def read(pk, sk, **kwargs):
        assert kwargs == {'consistent_read': True}
        return deepcopy(profile if sk == 'META' else state or None)

    def query(**kwargs):
        queries.append(kwargs)
        phase = int(state['phase'])
        offset = int((kwargs['exclusive_start_key'] or {}).get('position', 0))
        records = pages.get(phase, [])
        end = offset + kwargs['limit']
        page = {'Items': records[offset:end]}
        if end < len(records):
            page['LastEvaluatedKey'] = {'position': end}
        return page

    def write(transacts):
        assert len(transacts) <= sync.PAGE_SIZE + 2
        guard = transacts[0].get('Put') or transacts[0]['Delete']
        if guard['ExpressionAttributeValues'][':version'] != state.get('version'):
            raise DynamoDBTransactionError('ConditionalCheckFailed - newer checkpoint')
        writes.append(deepcopy(transacts))
        state.clear()
        if 'Item' in guard:
            state.update(deepcopy(guard['Item']))

    monkeypatch.setattr(sync, 'get_dynamodb_item', read)
    monkeypatch.setattr(sync, 'query_dynamodb_table', query)
    monkeypatch.setattr(sync, 'dynamodb_transact_write', write)
    return profile, state, writes, queries, pages


def run():
    background_handler.handler({'task': 'sync-profile', 'payload': {'user_id': 'creator'}}, None)


def test_large_profile_updates_every_status_and_comments_in_bounded_pages(propagation):
    profile, state, writes, queries, pages = propagation
    for phase in range(len(ArticleStatus) + 1):
        pages[phase] = [{'pk': f'POST#{phase}-{i}', 'sk': 'META' if phase < len(ArticleStatus)
                         else f'COMMENT#{i}'} for i in range(123)]
    run()
    assert not state
    updates = [item['Update'] for transaction in writes for item in transaction[1:] if 'Update' in item]
    assert len(updates) == 123 * (len(ArticleStatus) + 1)
    assert len({(item['Key']['pk'], item['Key']['sk']) for item in updates}) == len(updates)
    assert all('#updated_at' not in item['ExpressionAttributeNames'] for item in updates)
    assert all(item['ExpressionAttributeValues'][':new_user_name'] == profile['name'] for item in updates)
    comments = [item for item in updates if item['Key']['sk'].startswith('COMMENT#')]
    assert all(item['ExpressionAttributeValues'][':new_user_username'] == 'new-slug' for item in comments)
    assert all(item['ExpressionAttributeValues'][':new_user_avatar_filename'] == 'new.webp' for item in comments)
    assert len(queries) == 3 * (len(ArticleStatus) + 1)


def test_failed_page_resumes_after_last_committed_cursor(propagation, monkeypatch):
    _, state, writes, _, pages = propagation
    pages[0] = [{'pk': f'POST#{i}', 'sk': 'META'} for i in range(123)]
    write = sync.dynamodb_transact_write
    attempts = 0

    def fail_once(transacts):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError('temporary outage')
        write(transacts)

    monkeypatch.setattr(sync, 'dynamodb_transact_write', fail_once)
    with pytest.raises(RuntimeError, match='outage'):
        run()
    assert state['cursor'] == {'position': 50}
    run()
    assert not state
    keys = [item['Update']['Key']['pk'] for transaction in writes for item in transaction[1:] if 'Update' in item]
    assert len(keys) == len(set(keys)) == 123


def test_new_profile_edit_invalidates_in_flight_page(propagation, monkeypatch):
    profile, state, writes, _, pages = propagation
    pages[0] = [{'pk': 'POST#example', 'sk': 'META'}]
    query = sync.query_dynamodb_table
    changed = False

    def concurrent_edit(**kwargs):
        nonlocal changed
        page = query(**kwargs)
        if not changed:
            changed = True
            profile['name'] = 'Latest name'
            profile['username'] = None
            profile['avatar_filename'] = None
            state.update(version='newer-edit', phase=0)
        return page

    monkeypatch.setattr(sync, 'query_dynamodb_table', concurrent_edit)
    run()
    updates = [item['Update'] for transaction in writes for item in transaction[1:] if 'Update' in item]
    assert len(updates) == 1
    assert updates[0]['ExpressionAttributeValues'][':new_user_name'] == 'Latest name'
    assert 'user_slug' in updates[0]['ExpressionAttributeNames'].values()
    assert 'REMOVE' in updates[0]['UpdateExpression']


def test_time_budget_continues_with_durable_checkpoint(propagation, monkeypatch):
    _, state, writes, _, _ = propagation
    dispatch = Mock()
    monkeypatch.setattr(sync, 'dispatch_profile_sync', dispatch)
    sync.run_profile_sync_event({'task': 'sync-profile', 'payload': {'user_id': 'creator'}}, budget_seconds=0)
    dispatch.assert_called_once_with('creator')
    assert state == {'version': 'initial', 'phase': 0}
    assert not writes


@pytest.mark.parametrize('changes', [
    {'name': 'Changed'}, {'username': 'changed'}, {'username': None}, {'avatar_action': 'delete'},
])
def test_profile_and_checkpoint_commit_before_propagation(monkeypatch, changes):
    user = user_from_dynamodb({'id': 'creator', 'name': 'Creator', 'username': 'creator',
                               'avatar_filename': 'old.webp', 'created_at': 1})
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    monkeypatch.setattr(api_utils, 'drop_public_file', Mock())
    writes = []
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', lambda items: writes.append(deepcopy(items)))

    def propagate(user_id):
        assert user_id == user.id
        assert len(writes) == 1
        items = writes[0]
        assert len(items) <= 4
        assert any(item.get('Put', {}).get('Item', {}).get('sk') == sync.SYNC_KEY for item in items)
        assert any(item.get('Update', {}).get('Key') == {'pk': 'USER#creator', 'sk': 'META'} for item in items)

    resume = Mock(side_effect=propagate)
    monkeypatch.setattr(api_utils, 'dispatch_profile_sync', resume)
    api_utils.update_user(user, UpdateUserDTO(**changes), user, None)
    resume.assert_called_once_with(user.id)


def test_failed_username_reservation_never_propagates(monkeypatch):
    from shared_utils import SlugDuplicationError

    user = user_from_dynamodb({'id': 'creator', 'name': 'Creator', 'username': 'creator', 'created_at': 1})
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', Mock(
        side_effect=DynamoDBTransactionError('ConditionalCheckFailed - username taken')))
    resume = Mock()
    monkeypatch.setattr(api_utils, 'dispatch_profile_sync', resume)
    with pytest.raises(SlugDuplicationError):
        api_utils.update_user(user, UpdateUserDTO(username='taken'), user, None)
    resume.assert_not_called()


def test_unchanged_profile_save_resumes_pending_propagation(monkeypatch):
    user = user_from_dynamodb({'id': 'creator', 'name': 'Creator', 'created_at': 1})
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    resume = Mock()
    monkeypatch.setattr(api_utils, 'dispatch_profile_sync', resume)
    api_utils.update_user(user, UpdateUserDTO(name='Creator'), user, None)
    resume.assert_called_once_with(user.id)


def test_dispatch_queues_worker_without_running_propagation(monkeypatch):
    import json

    monkeypatch.setitem(sync.config, 'background_function_name', 'background-worker')
    monkeypatch.setattr(sync, 'get_dynamodb_item', Mock(return_value={'version': 'pending', 'phase': 0}))
    client = Mock()
    client.invoke.return_value = {'StatusCode': 202}
    monkeypatch.setattr(sync.boto3, 'client', Mock(return_value=client))
    run_worker = Mock()
    monkeypatch.setattr(sync, 'run_profile_sync_event', run_worker)
    query = Mock()
    monkeypatch.setattr(sync, 'query_dynamodb_table', query)

    sync.dispatch_profile_sync('creator')

    call = client.invoke.call_args.kwargs
    assert call['InvocationType'] == 'Event'
    assert call['FunctionName'] == 'background-worker'
    assert json.loads(call['Payload']) == {'task': 'sync-profile', 'payload': {'user_id': 'creator'}}
    run_worker.assert_not_called()
    query.assert_not_called()


def test_local_dispatch_submits_worker_without_waiting(monkeypatch):
    monkeypatch.setitem(sync.config, 'background_function_name', None)
    monkeypatch.setitem(sync.config, 'app_stage', 'local')
    monkeypatch.setattr(sync, 'get_dynamodb_item', Mock(return_value={'version': 'pending', 'phase': 0}))
    executor = Mock()
    monkeypatch.setattr(sync, '_local_workers', executor)
    query = Mock()
    monkeypatch.setattr(sync, 'query_dynamodb_table', query)

    sync.dispatch_profile_sync('creator')

    executor.submit.assert_called_once_with(sync.run_profile_sync_event, {
        'task': 'sync-profile', 'payload': {'user_id': 'creator'},
    })
    query.assert_not_called()
