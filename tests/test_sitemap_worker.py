"""Sitemap dispatch returns acceptance only after queuing authorized work."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import api_utils
import sitemap_service
from sitemap_handler import get_web_request, handler
from app_config import get_config


def test_sitemap_dispatch_is_async_and_checks_permission(monkeypatch):
    monkeypatch.setitem(get_config(), 'sitemap_function_name', 'sitemap-worker')
    authorize = Mock()
    monkeypatch.setattr(sitemap_service, 'verify_authorization', authorize)
    client = Mock()
    client.invoke.return_value = {'StatusCode': 202}
    monkeypatch.setattr(sitemap_service.boto3, 'client', lambda _: client)
    generate = Mock()
    monkeypatch.setattr(api_utils, 'generate_sitemap', generate)
    user = SimpleNamespace(id='admin')
    result = sitemap_service.submit_sitemap(user, get_web_request())
    assert result['status'] == 'accepted'
    assert client.invoke.call_args.kwargs['InvocationType'] == 'Event'
    assert json.loads(client.invoke.call_args.kwargs['Payload'])['payload']['actor_user_id'] == 'admin'
    generate.assert_not_called()
    authorize.assert_called_once_with(user, sitemap_service.Permission.GENERATE_SITEMAP)
    authorize.side_effect = PermissionError
    client.reset_mock()
    with pytest.raises(PermissionError):
        sitemap_service.submit_sitemap(user, get_web_request())
    client.invoke.assert_not_called()


def test_sitemap_worker_reloads_actor_and_does_not_hide_failure(monkeypatch):
    user = SimpleNamespace(id='admin')
    load = Mock(return_value=user)
    monkeypatch.setattr(sitemap_service, 'get_user', load)
    generate = Mock(side_effect=PermissionError('revoked'))
    monkeypatch.setattr(api_utils, 'generate_sitemap', generate)
    with pytest.raises(PermissionError, match='revoked'):
        handler({'task': 'generate-sitemap', 'payload': {'actor_user_id': user.id}}, None)
    load.assert_called_once_with('admin')
    generate.side_effect = None
    generate.return_value = (10, 'https://static.example/sitemap.xml')
    assert handler({'task': 'generate-sitemap', 'payload': {'actor_user_id': user.id}}, None)['urls_count'] == 10


@pytest.mark.parametrize('event', [{}, {'task': 'unknown'}, {'task': 'generate-sitemap', 'payload': {}}])
def test_sitemap_worker_rejects_invalid_events(event):
    with pytest.raises(ValueError):
        handler(event, None)
