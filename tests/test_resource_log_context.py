"""Activity notifications use navigable resource URLs without requiring HTTP in scripts."""
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import api_utils
import shared_utils
from app_config import get_config
from article_dtos import ArticleCommentDTO
from basic_dtos import UserTokenDTO
from sitemap_handler import get_web_request
from tag_subscription_dtos import TagSubscriptionDTO


@pytest.fixture
def resources(monkeypatch):
    monkeypatch.setitem(get_config(), 'web_base_url', 'https://site.example')
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', Mock())
    user = shared_utils.user_from_dynamodb({'id': 'owner', 'name': 'Owner', 'username': 'author', 'created_at': 1})
    article = shared_utils.article_from_dynamodb({
        'id': 'article-id', 'user_id': user.id, 'user_slug': user.username, 'title': 'Example',
        'post_slug': 'example', 'content': 'Example body', 'status': 'published', 'rating_sk': 0, 'created_at': 1,
    })
    logger = Mock()
    monkeypatch.setattr(api_utils, 'logger', logger)
    return user, article, logger


def test_comment_notification_links_to_the_rendered_comment_anchor(resources):
    user, article, logger = resources
    comment = api_utils.create_article_comment(article, ArticleCommentDTO(text='A comment'), user, get_web_request())
    assert logger.info.call_args.kwargs['extra']['context'] == (
        'https://site.example/@author/example#comment-' + comment.id.replace('#', '-')
    )


def test_subscription_notification_links_to_the_matching_collection(resources):
    user, _, logger = resources
    api_utils.create_tag_subscription(TagSubscriptionDTO(tags=['cache', 'queues']), user, req=get_web_request())
    assert logger.info.call_args.kwargs['extra']['context'] == 'https://site.example/cache/queues/articles'


def test_category_notification_uses_its_filtered_collection_and_skips_existing(resources, monkeypatch):
    user, _, logger = resources
    monkeypatch.setattr(api_utils, 'find_category', Mock(return_value=None))
    category = api_utils.create_category('other', 'Other', 'Other engineering articles.', user, req=get_web_request())
    assert logger.info.call_args.kwargs['extra']['context'] == 'https://site.example/articles?category=other'
    logger.reset_mock()
    monkeypatch.setattr(api_utils, 'find_category', Mock(return_value=category))
    assert api_utils.create_category('other', 'Other', 'Other engineering articles.', user, req=get_web_request()) == category
    logger.info.assert_not_called()


def test_registration_notification_uses_saved_profile_url_only_for_new_users(resources, monkeypatch):
    monkeypatch.setattr(shared_utils, 'get_user_by_user_token', Mock(return_value=None))
    monkeypatch.setattr(shared_utils, 'dynamodb_transact_write', Mock())
    monkeypatch.setattr(shared_utils, 'build_user_username', lambda *args: 'new-author')
    logger = Mock()
    monkeypatch.setattr(shared_utils, 'logger', logger)
    token = UserTokenDTO(sub='subject', iss='provider', name='New Author')
    user = shared_utils.upsert_user_by_user_token(token, req=get_web_request())
    assert logger.info.call_args.kwargs['extra']['context'] == 'https://site.example/@new-author'
    logger.reset_mock()
    monkeypatch.setattr(shared_utils, 'get_user_by_user_token', Mock(return_value=user))
    shared_utils.upsert_user_by_user_token(token, req=get_web_request())
    logger.info.assert_not_called()


def test_requestless_comment_notification_retains_identifiers(resources):
    user, article, logger = resources
    comment = api_utils.create_article_comment(article, ArticleCommentDTO(text='A comment'), user, None)
    assert logger.info.call_args.kwargs['extra']['context'] == {'article_id': article.id, 'comment_id': comment.id}


def test_contact_submission_logs_message_identity_and_authenticated_sender(resources, monkeypatch):
    user, _, logger = resources
    table = Mock()
    monkeypatch.setattr(api_utils, 'get_dynamodb_table', lambda: table)
    monkeypatch.setattr(api_utils, 'is_prod', lambda: False)
    message = api_utils.create_contact_message(
        api_utils.ContactMessageDTO(name='Reader', email='reader@example.com', message='A useful message'),
        user, req=get_web_request(),
    )
    assert table.put_item.call_args.kwargs['Item']['message_id'] == message.id
    assert logger.info.call_args.kwargs['extra']['context'] == {
        'message_id': message.id, 'name': 'Reader', 'email': 'reader@example.com',
        'message': 'A useful message', 'by': 'https://site.example/@author',
    }
