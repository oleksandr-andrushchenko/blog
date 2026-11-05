"""Reports persist their target and counters in the same guarded transaction."""
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import api_utils
from basic_dtos import ContactMessageDTO
from shared_utils import DynamoDBTransactionError, article_from_dynamodb, user_from_dynamodb
from web import HTTPException, RequestValidationError, parse_dto


def resources():
    owner = user_from_dynamodb({'id': 'owner', 'name': 'Creator', 'created_at': 1})
    article = article_from_dynamodb({
        'id': 'example', 'user_id': owner.id, 'title': 'Example', 'post_slug': 'example',
        'content': 'Write a story.', 'category': 'architecture',
        'status': 'published', 'rating_sk': 0, 'created_at': 1,
    })
    return owner, article


def test_report_counters_default_to_zero_for_legacy_records():
    user, article = resources()
    assert user.reports_count == user.article_reports_count == article.reports_count == 0


@pytest.mark.parametrize(('field', 'value'), [
    ('name', None), ('name', ' '), ('name', 'x'),
    ('email', None), ('email', 'not-an-email'),
    ('message', None), ('message', 'tiny'), ('message', 'x' * 1001),
])
def test_contact_validation_identifies_the_visible_report_field(field, value):
    payload = {'name': 'Example Reporter', 'email': 'reporter@example.com', 'message': 'Please review this.'}
    with pytest.raises(RequestValidationError) as error:
        parse_dto(ContactMessageDTO, {**payload, field: value})
    assert list(error.value.details) == [field]


@pytest.mark.parametrize('kind', ['user', 'article'])
@pytest.mark.parametrize('authenticated', [False, True])
def test_report_uses_one_transaction_and_preserves_modification_dates(monkeypatch, kind, authenticated):
    owner, article = resources()
    target = owner if kind == 'user' else article
    actor = user_from_dynamodb({'id': 'reader', 'name': 'Reader', 'created_at': 1}) if authenticated else None
    writer = Mock()
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', writer)
    monkeypatch.setattr(api_utils, 'is_prod', lambda: False)
    dto = ContactMessageDTO(name='Example Reporter', email='reporter@example.com', message='Please review this.')
    api_utils.create_contact_message(dto, actor, report_target=target)
    writer.assert_called_once()
    writes = writer.call_args.args[0]
    message = writes[0]['Put']['Item']
    assert message['target_user_id' if kind == 'user' else 'article_id'] == target.id
    assert ('user_id' in message) is authenticated
    updates = [write['Update'] for write in writes if 'Update' in write]
    assert len(updates) == (1 if kind == 'user' else 2)
    assert len({tuple(operation['Key'].values()) for operation in updates}) == len(updates)
    for operation in updates:
        assert 'updated_at' not in operation['ExpressionAttributeNames'].values()
        assert 'attribute_exists(pk)' in operation['ConditionExpression']
        if operation['Key']['pk'].startswith('USER#'):
            assert 'attribute_not_exists(deletion_id)' in operation['ConditionExpression']
    if kind == 'article':
        assert updates[0]['ExpressionAttributeValues'][':report_status'] == 'published'
        assert updates[0]['ExpressionAttributeValues'][':report_owner'] == owner.id
        assert updates[1]['ExpressionAttributeNames']['#delta_article_reports_count'] == 'article_reports_count'


def test_report_rejects_erasure_or_visibility_race_without_logging_success(monkeypatch):
    _, target = resources()
    monkeypatch.setattr(api_utils, 'is_prod', lambda: False)
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write',
                        Mock(side_effect=DynamoDBTransactionError('ConditionalCheckFailed')))
    log = Mock()
    monkeypatch.setattr(api_utils, 'logger', log)
    dto = ContactMessageDTO(name='Example Reporter', email='reporter@example.com', message='Please review this.')
    with pytest.raises(HTTPException) as error:
        api_utils.create_contact_message(dto, report_target=target)
    assert error.value.status_code == 409
    log.info.assert_not_called()


@pytest.mark.parametrize('kind', ['user', 'article'])
def test_owners_cannot_report_themselves_even_with_root_permissions(monkeypatch, kind):
    owner, article = resources()
    owner.permissions = ['root']
    writer, sns = Mock(), Mock()
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', writer)
    monkeypatch.setattr(api_utils, 'get_sns_client', sns)
    monkeypatch.setattr(api_utils, 'is_prod', lambda: True)
    dto = ContactMessageDTO(name='Example Reporter', email='reporter@example.com', message='Please review this.')
    with pytest.raises(HTTPException) as error:
        api_utils.create_contact_message(dto, owner, report_target=owner if kind == 'user' else article)
    assert error.value.status_code == 403
    writer.assert_not_called()
    sns.assert_not_called()


def test_operator_notification_failure_preserves_committed_report(monkeypatch):
    _, target = resources()
    calls = []
    monkeypatch.setattr(api_utils, 'is_prod', lambda: True)
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', lambda writes: calls.append('stored'))
    monkeypatch.setattr(api_utils, 'get_contact_topic_arn', lambda: 'test-contact-topic')

    def publish(**kwargs):
        assert calls == ['stored']
        calls.append('notified')
        raise RuntimeError('Delivery unavailable')

    client = Mock()
    client.publish.side_effect = publish
    monkeypatch.setattr(api_utils, 'get_sns_client', lambda: client)
    dto = ContactMessageDTO(name='Example Reporter', email='reporter@example.com', message='Please review this.')
    result = api_utils.create_contact_message(dto, report_target=target)
    assert result.message == dto.message
    assert calls == ['stored', 'notified']


def test_failed_report_never_notifies_operator(monkeypatch):
    _, target = resources()
    monkeypatch.setattr(api_utils, 'is_prod', lambda: True)
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write',
                        Mock(side_effect=DynamoDBTransactionError('ConditionalCheckFailed')))
    client = Mock()
    monkeypatch.setattr(api_utils, 'get_sns_client', client)
    dto = ContactMessageDTO(name='Example Reporter', email='reporter@example.com', message='Please review this.')
    with pytest.raises(HTTPException):
        api_utils.create_contact_message(dto, report_target=target)
    client.assert_not_called()
