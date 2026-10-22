"""Preference defaults, committed actions, and best-effort account email delivery."""
from email import policy
from email.parser import BytesParser
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import api_utils
import email_service
from article_dtos import UpdateArticleImpressionDTO, UpdateArticleStatusDTO
from shared_utils import article_from_dynamodb, user_from_dynamodb
from user_dtos import UpdateUserDTO, UpdateUserImpressionDTO

PREFERENCES = ('notify_article_status', 'notify_user_impressions', 'notify_article_impressions', 'notify_tag_articles')


@pytest.mark.parametrize('preference', PREFERENCES)
def test_email_preferences_default_enabled_and_preserve_explicit_opt_out(preference):
    item = {'id': 'owner', 'name': 'Creator', 'created_at': 1}
    assert getattr(user_from_dynamodb(item), preference) is True
    assert getattr(user_from_dynamodb({**item, preference: False}), preference) is False
    assert UpdateUserDTO(**{preference: False}).get_changes() == {preference: False}


@pytest.mark.parametrize('preference', PREFERENCES)
@pytest.mark.parametrize('invalid', [None, 0, 'false'])
def test_email_preferences_require_booleans(preference, invalid):
    with pytest.raises(ValueError, match='must be true or false'):
        UpdateUserDTO(**{preference: invalid})


@pytest.fixture
def actions(monkeypatch):
    owner = user_from_dynamodb({'id': 'owner', 'name': 'Creator', 'user_email_pk': 'owner@example.com', 'created_at': 1})
    actor = user_from_dynamodb({'id': 'actor', 'name': '<Actor>', 'username': 'actor',
                                'permissions': ['root'], 'created_at': 1})
    resource = article_from_dynamodb({
        'id': 'article', 'user_id': owner.id, 'title': '<Example article>', 'post_slug': 'example-article',
        'content': '<p>Example article.</p>', 'status': 'unpublished',
        'rating_sk': 0, 'created_at': 1,
    })
    write, send = Mock(), Mock()
    environment = Environment(loader=FileSystemLoader(ROOT / 'api-lambda/templates'))
    monkeypatch.setattr(api_utils, 'get_html_content', lambda name, data: environment.get_template(name).render(
        **data, site_name='Example Site', web_base_url='https://example.com'))
    monkeypatch.setattr(api_utils, 'get_url', lambda *args, **kwargs: 'https://example.com/users/owner/edit')
    monkeypatch.setattr(api_utils, 'get_article_url', lambda *args, **kwargs: 'https://example.com/articles/article')
    monkeypatch.setattr(api_utils, 'get_user_url', lambda *args, **kwargs: 'https://example.com/@actor')
    monkeypatch.setattr(api_utils, 'get_user', lambda user_id: owner)
    monkeypatch.setattr(api_utils, 'find_article_impression', Mock(return_value=None))
    monkeypatch.setattr(api_utils, 'find_user_impression', Mock(return_value=None))
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', write)
    monkeypatch.setattr(api_utils, 'dispatch_article_published_event', Mock())
    monkeypatch.setattr(api_utils, '_notify_article_search_engines', Mock())
    monkeypatch.setattr(api_utils, 'send_email', send)
    return SimpleNamespace(owner=owner, actor=actor, article=resource, write=write, send=send)


@pytest.mark.parametrize('manual', [True, False])
@pytest.mark.parametrize('status', ['published', 'rejected', 'unpublished'])
def test_only_actual_manual_status_changes_send_email(actions, manual, status):
    actions.write.side_effect = lambda _: actions.send.assert_not_called()
    api_utils.update_article_status(actions.article, UpdateArticleStatusDTO(status=status, comment='Review <reason>'),
                                    actions.actor, object(), manual=manual)
    assert actions.send.call_count == int(manual and status != 'unpublished')
    if actions.send.called:
        recipient, subject, text, html = actions.send.call_args.args
        assert recipient == 'owner@example.com'
        assert subject == 'Your article status changed'
        assert 'Review <reason>' in text
        assert 'Review &lt;reason&gt;' in html
        assert '<Example article>' not in html
        assert '#notifications' in text and '#notifications' in html


@pytest.mark.parametrize('action', ['follow', 'block'])
@pytest.mark.parametrize('existing', [None, 'follow', 'block'])
def test_only_new_profile_reactions_send_email(actions, action, existing):
    api_utils.find_user_impression.return_value = SimpleNamespace(action=existing) if existing else None
    actions.write.side_effect = lambda _: actions.send.assert_not_called()
    api_utils.update_user_impression(actions.owner, UpdateUserImpressionDTO(action=action), actions.actor, object())
    assert actions.send.call_count == int(existing is None)
    if actions.send.called:
        assert '<Actor>' in actions.send.call_args.args[2]
        assert '&lt;Actor&gt;' in actions.send.call_args.args[3]


@pytest.mark.parametrize('action', ['like', 'dislike'])
@pytest.mark.parametrize('existing', [None, 'like', 'dislike'])
def test_only_new_article_reactions_send_email(actions, action, existing):
    api_utils.find_article_impression.return_value = SimpleNamespace(action=existing) if existing else None
    actions.write.side_effect = lambda _: actions.send.assert_not_called()
    api_utils.update_article_impression(actions.article, UpdateArticleImpressionDTO(action=action), actions.actor, object())
    assert actions.send.call_count == int(existing is None)


@pytest.mark.parametrize('event', ['status', 'profile', 'article'])
@pytest.mark.parametrize('failure', ['opt-out', 'database', 'email'])
def test_preferences_and_failures_respect_action_commit(actions, event, failure):
    preference = {'status': 'notify_article_status', 'profile': 'notify_user_impressions',
                  'article': 'notify_article_impressions'}[event]
    if failure == 'opt-out':
        setattr(actions.owner, preference, False)
    elif failure == 'database':
        actions.write.side_effect = RuntimeError('Database unavailable')
    else:
        actions.send.side_effect = RuntimeError('Email unavailable')

    def act():
        if event == 'status':
            api_utils.update_article_status(actions.article, UpdateArticleStatusDTO(status='published'),
                                            actions.actor, object(), manual=True)
        elif event == 'profile':
            api_utils.update_user_impression(actions.owner, UpdateUserImpressionDTO(action='follow'),
                                             actions.actor, object())
        else:
            api_utils.update_article_impression(actions.article, UpdateArticleImpressionDTO(action='like'),
                                               actions.actor, object())

    if failure == 'database':
        with pytest.raises(RuntimeError, match='Database'):
            act()
    else:
        act()
    assert actions.send.call_count == int(failure == 'email')


def test_self_article_reaction_does_not_email_owner(actions):
    actions.owner.permissions = ['root']
    api_utils.update_article_impression(actions.article, UpdateArticleImpressionDTO(action='like'), actions.owner, object())
    actions.send.assert_not_called()


def test_local_delivery_saves_text_and_html_without_contacting_ses(monkeypatch, tmp_path):
    monkeypatch.setattr(email_service, 'is_prod', lambda: False)
    monkeypatch.setattr(email_service, 'get_email_files_dir', lambda: str(tmp_path))
    monkeypatch.setattr(email_service, 'get_ses_from_email', lambda: '')
    client = Mock()
    monkeypatch.setattr(email_service, 'get_ses_client', client)
    email_service.send_email('owner@example.com', 'Example notification', 'Plain text', '<p>HTML body</p>')
    message = BytesParser(policy=policy.default).parsebytes(next(tmp_path.glob('*.eml')).read_bytes())
    assert message['To'] == 'owner@example.com'
    assert 'Plain text' in message.get_body('plain').get_content()
    assert '<p>HTML body</p>' in message.get_body('html').get_content()
    client.assert_not_called()


def test_production_delivery_uses_ses_with_both_bodies(monkeypatch):
    monkeypatch.setattr(email_service, 'is_prod', lambda: True)
    monkeypatch.setattr(email_service, 'get_ses_from_email', lambda: 'sender@example.com')
    client = Mock()
    monkeypatch.setattr(email_service, 'get_ses_client', lambda: client)
    email_service.send_email('owner@example.com', 'Example notification', 'Plain text', '<p>HTML body</p>')
    payload = client.send_email.call_args.kwargs
    assert payload['Source'] == 'sender@example.com'
    assert payload['Destination'] == {'ToAddresses': ['owner@example.com']}
    assert payload['Message']['Body']['Text']['Data'] == 'Plain text'
    assert payload['Message']['Body']['Html']['Data'] == '<p>HTML body</p>'
