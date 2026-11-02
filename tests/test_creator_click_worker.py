"""Click acknowledgement precedes processing; accepted events survive worker delays."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda', 'web-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import api_utils
import creator_click_worker as worker
import background_handler
from creator_links import encode_creator_click
from web import HTTPException


def payload():
    return {'owner_id': 'creator', 'link_id': 'studio', 'kind': 'support',
            'article_id': None, 'actor_id': None, 'event_id': 'a' * 64}


def test_api_queues_unique_events_and_keeps_replay_identity(monkeypatch):
    monkeypatch.setitem(worker.config, 'app_secret', 'test-click-secret')
    dispatch, process = Mock(), Mock()
    monkeypatch.setattr(worker, 'dispatch_creator_click', dispatch)
    monkeypatch.setattr(api_utils, 'process_creator_link_click', process)
    token = encode_creator_click('creator', 'support', 'studio')
    api_utils.notify_creator_link_click(token, None)
    api_utils.notify_creator_link_click(token, None)
    api_utils.notify_creator_link_click(encode_creator_click('creator', 'support', 'studio'), None)
    first, replay, separate = [call.args[0] for call in dispatch.call_args_list]
    assert first == replay and first['event_id'] != separate['event_id']
    assert set(first) == set(payload())
    assert first['owner_id'] == 'creator'
    process.assert_not_called()


def test_dispatch_uses_async_invocation_with_bounded_timeout(monkeypatch):
    monkeypatch.setitem(worker.config, 'background_function_name', 'background-worker')
    client = Mock()
    client.invoke.return_value = {'StatusCode': 202}
    factory = Mock(return_value=client)
    monkeypatch.setattr(worker.boto3, 'client', factory)
    worker.dispatch_creator_click(payload())
    args = client.invoke.call_args.kwargs
    assert args['InvocationType'] == 'Event' and args['FunctionName'] == 'background-worker'
    assert json.loads(args['Payload']) == {'task': 'creator-link-click', 'payload': payload()}
    timeouts = factory.call_args.kwargs['config']
    assert timeouts.connect_timeout == 2 and timeouts.read_timeout == 5
    client.invoke.return_value = {'StatusCode': 500}
    with pytest.raises(HTTPException) as error:
        worker.dispatch_creator_click(payload())
    assert error.value.status_code == 503


def test_local_dispatch_does_not_wait_for_processing(monkeypatch):
    monkeypatch.setitem(worker.config, 'background_function_name', None)
    monkeypatch.setitem(worker.config, 'app_stage', 'local')
    executor = Mock()
    monkeypatch.setattr(worker, '_local_workers', executor)
    worker.dispatch_creator_click(payload())
    executor.submit.assert_called_once_with(worker.run_creator_click_event, {
        'task': 'creator-link-click', 'payload': payload(),
    })
    executor.submit.return_value.result.assert_not_called()


def test_worker_does_not_expire_already_accepted_tokens_again(monkeypatch):
    request = object()
    monkeypatch.setattr(worker, 'get_web_request', lambda: request)
    process = Mock()
    monkeypatch.setattr(api_utils, 'process_creator_link_click', process)
    background_handler.handler({'task': 'creator-link-click', 'payload': payload()}, None)
    process.assert_called_once_with(payload(), request)


@pytest.mark.parametrize('status', [403, 404, 409, 500])
def test_worker_skips_removed_links_but_retries_processing_errors(monkeypatch, status):
    monkeypatch.setattr(worker, 'get_web_request', Mock())
    monkeypatch.setattr(api_utils, 'process_creator_link_click', Mock(side_effect=HTTPException(status)))
    event = {'task': 'creator-link-click', 'payload': payload()}
    if status in (403, 404):
        worker.run_creator_click_event(event)
    else:
        with pytest.raises(HTTPException):
            worker.run_creator_click_event(event)


@pytest.mark.parametrize('change', [{'event_id': ''}, {'owner_id': None}, {'kind': 'unknown'}, {'actor_id': 123}])
def test_worker_rejects_malformed_events(monkeypatch, change):
    process = Mock()
    monkeypatch.setattr(api_utils, 'process_creator_link_click', process)
    with pytest.raises(ValueError):
        worker.run_creator_click_event({'task': 'creator-link-click', 'payload': payload() | change})
    process.assert_not_called()
