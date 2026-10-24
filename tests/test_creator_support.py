"""Support links preserve counters during edits and resolve only published selections."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys
from unittest.mock import Mock
import uuid

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda', 'web-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import api_utils
import creator_click_service
import shared_utils
import web_utils
from creator_links import decode_creator_click, encode_creator_click, get_creator_link
from shared_utils import user_from_dynamodb, article_from_dynamodb
from user_dtos import UpdateUserDTO
from article_dtos import UpdateArticleDTO
from web import HTTPException, Request, RequestValidationError


@pytest.fixture
def saved_support(monkeypatch):
    from test_utils import dynamodb_table
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    identifier = 'support-' + uuid.uuid4().hex
    user_item = {'pk': 'USER#' + identifier, 'sk': 'META', 'id': identifier, 'name': 'Author', 'created_at': 1,
                 'support_links': [{'id': 'membership', 'name': 'Membership', 'url': 'https://support.example/author',
                                    'text': '', 'clicks_count': 0}]}
    article_item = {'pk': 'POST#' + identifier, 'sk': 'META', 'id': identifier, 'user_id': identifier,
                    'title': 'Example', 'content': 'Article body', 'post_slug': 'example', 'status': 'published',
                    'rating_sk': 1, 'created_at': 1, 'updated_at': 2, 'support_providers': ['membership']}
    for item in (user_item, article_item):
        dynamodb_table.put_item(Item=item)
    yield dynamodb_table, user_from_dynamodb(user_item), article_from_dynamodb(article_item)
    for item in (user_item, article_item):
        dynamodb_table.delete_item(Key={'pk': item['pk'], 'sk': 'META'})


def test_concurrent_support_clicks_and_profile_edits_preserve_totals(saved_support, monkeypatch):
    table, user, article = saved_support
    # Initialize the nested map once, then exercise concurrent increments and profile editing.
    creator_click_service.record_creator_link_click(user, 'membership', article)
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: creator_click_service.record_creator_link_click(user, 'membership', article), range(8)))
    key = {'pk': 'USER#' + user.id, 'sk': 'META'}
    saved = table.get_item(Key=key, ConsistentRead=True)['Item']
    assert saved['support_links'][0]['clicks_count'] == 9
    assert table.get_item(Key={'pk': 'POST#' + article.id, 'sk': 'META'}, ConsistentRead=True)['Item']['support_link_click_counts']['membership'] == 9
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    api_utils.update_user(user, UpdateUserDTO(support_links=[{'id': 'membership', 'name': 'New name',
                          'url': 'https://support.example/new'}]), user, None)
    updated = table.get_item(Key=key, ConsistentRead=True)['Item']
    assert updated['support_links'][0]['name'] == 'New name'
    assert updated['support_links'][0]['clicks_count'] == 9
    assert updated['created_at'] == 1


def test_article_support_selection_validates_actual_owner(saved_support, monkeypatch):
    _, user, article = saved_support
    assert get_creator_link(user, 'support', 'membership', article).name == 'Membership'
    article.support_providers = []
    with pytest.raises(ValueError):
        get_creator_link(user, 'support', 'membership', article)
    article.support_providers = None
    assert get_creator_link(user, 'support', 'membership', article)
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    monkeypatch.setattr(api_utils, 'get_user', lambda _: user)
    with pytest.raises(RequestValidationError):
        api_utils.update_article(article, UpdateArticleDTO(support_providers=['missing']), user, None)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://support.example', 'https://user:pass@support.example',
                                  'https://support.example/with space', 'https://support.example\\bad'])
def test_support_links_reject_unsafe_destinations(url):
    with pytest.raises(ValueError):
        UpdateUserDTO(support_links=[{'id': 'tip', 'name': 'Tip', 'url': url}])


def test_support_click_counts_are_server_owned():
    with pytest.raises(ValueError):
        UpdateUserDTO(support_links=[{'id': 'tip', 'name': 'Tip', 'url': 'https://support.example', 'clicks_count': 99}])


def test_signed_click_notifications_reject_tampering_and_self_clicks(saved_support, monkeypatch):
    _, user, article = saved_support
    from app_config import get_config
    monkeypatch.setitem(get_config(), 'app_secret', 'synthetic-test-secret')
    token = encode_creator_click(user.id, 'support', 'membership', article.id, user.id)
    assert decode_creator_click(token)['article_id'] == article.id
    with pytest.raises(ValueError):
        decode_creator_click(token + 'bad')
    monkeypatch.setattr(api_utils, 'find_user', lambda _: user)
    monkeypatch.setattr(api_utils, 'get_article', lambda *args: article)
    record = Mock()
    monkeypatch.setattr(creator_click_service, 'record_creator_link_click', record)
    api_utils.notify_creator_link_click(token, None)
    record.assert_not_called()


def test_support_navigation_requires_same_origin_post(saved_support, monkeypatch):
    _, user, article = saved_support
    monkeypatch.setattr(web_utils, 'get_web_base_url', lambda: 'https://site.example')
    def request(method, origin):
        return Request({'type': 'http', 'method': method, 'path': '/', 'headers': [(b'origin', origin.encode())]})
    with pytest.raises(HTTPException) as error:
        web_utils.redirect_creator_link(user, 'membership', request('GET', 'https://site.example'), kind='support')
    assert error.value.status_code == 405
    with pytest.raises(HTTPException) as error:
        web_utils.redirect_creator_link(user, 'membership', request('POST', 'https://evil.example'), kind='support')
    assert error.value.status_code == 403


def test_invalid_click_notification_returns_authentication_error():
    with pytest.raises(HTTPException) as error:
        api_utils.notify_creator_link_click('invalid', None)
    assert error.value.status_code == 401
