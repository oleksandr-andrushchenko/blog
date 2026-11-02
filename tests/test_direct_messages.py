"""Direct-message writes must guard privacy at commit time and avoid uncertain retries."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "api-lambda")]

import direct_message_service as service
from direct_messages import DirectMessageDTO
from shared_utils import DynamoDBTransactionError, user_from_dynamodb
from web import HTTPException


@pytest.fixture
def message_writer(monkeypatch):
    sender = SimpleNamespace(id="sender", status="active")
    peer = SimpleNamespace(id="recipient", status="active")
    monkeypatch.setattr(service, "get_dynamodb_item", Mock(return_value=None))
    monkeypatch.setattr(service, "get_dynamodb_table_name", lambda: "app_test")
    monkeypatch.setattr(service, "message_peer", lambda *args: peer)
    monkeypatch.setattr(service, "can_send_message", Mock(return_value=True))
    monkeypatch.setattr(service, "get_url", lambda *args, **kwargs: "https://example.com/messages/recipient")
    monkeypatch.setattr(service, "get_user_url", lambda *args, **kwargs: "https://example.com/@sender")
    monkeypatch.setattr(service.time, "sleep", Mock())
    monkeypatch.setattr(service.logger, "info", Mock())
    monkeypatch.setattr(service, "_notify_direct_message", Mock())
    write = Mock()
    monkeypatch.setattr(service, "dynamodb_transact_write", write)
    return sender, peer, write


def test_send_guards_status_and_both_blocks_in_the_owning_transaction(message_writer):
    sender, peer, write = message_writer
    dto = DirectMessageDTO("Private text that must stay out of logs.", "00000000-0000-4000-8000-000000000001")
    result = service.send_direct_message(sender, peer.id, dto)
    assert result["text"] == dto.text
    guards = [item["ConditionCheck"] for item in write.call_args.args[0] if "ConditionCheck" in item]
    assert {(guard["Key"]["pk"], guard["Key"]["sk"]) for guard in guards} == {
        ("USER#sender", "META"), ("USER#recipient", "META"),
        ("USER#sender", "REL#recipient"), ("USER#recipient", "REL#sender"),
    }
    assert all(":active" in guard["ExpressionAttributeValues"] if guard["Key"]["sk"] == "META"
               else ":block" in guard["ExpressionAttributeValues"] for guard in guards)
    assert dto.text not in str(service.logger.info.call_args)


def test_uncertain_send_failures_are_not_automatically_retried(message_writer):
    sender, peer, write = message_writer
    write.side_effect = ClientError({"Error": {"Code": "InternalServerError"}}, "TransactWriteItems")
    with pytest.raises(ClientError):
        service.send_direct_message(sender, peer.id, DirectMessageDTO("Keep my draft", "00000000-0000-4000-8000-000000000002"))
    assert write.call_count == 1
    service._notify_direct_message.assert_not_called()
    service.logger.info.assert_not_called()


def test_confirmed_conflicts_recheck_block_state_before_retrying(message_writer, monkeypatch):
    sender, peer, write = message_writer

    def conflict(*args):
        try:
            raise ClientError({"Error": {"Code": "TransactionCanceledException"},
                               "CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}, "TransactWriteItems")
        except ClientError:
            raise DynamoDBTransactionError("A participant blocked the conversation")

    write.side_effect = conflict
    monkeypatch.setattr(service, "can_send_message", Mock(side_effect=[True, False]))
    with pytest.raises(HTTPException) as error:
        service.send_direct_message(sender, peer.id, DirectMessageDTO("A guarded message", "00000000-0000-4000-8000-000000000003"))
    assert error.value.status_code == 403
    assert write.call_count == 1
    service._notify_direct_message.assert_not_called()
    service.logger.info.assert_not_called()



def test_message_email_is_attempted_only_after_successful_persistence(message_writer):
    sender, peer, write = message_writer
    order = []
    write.side_effect = lambda *args: order.append("write")
    service._notify_direct_message.side_effect = lambda *args: order.append("email")
    service.send_direct_message(sender, peer.id, DirectMessageDTO("A saved message", "00000000-0000-4000-8000-000000000004"))
    assert order == ["write", "email"]


def test_email_failure_preserves_the_committed_message(message_writer):
    sender, peer, write = message_writer
    service._notify_direct_message.side_effect = RuntimeError("Email transport unavailable")
    result = service.send_direct_message(sender, peer.id, DirectMessageDTO("A saved message", "00000000-0000-4000-8000-000000000005"))
    assert result["text"] == "A saved message"
    assert write.call_count == 1
    service._notify_direct_message.assert_called_once_with(sender, peer.id, "00000000-0000-4000-8000-000000000005")


@pytest.mark.parametrize('preference,email,status,blocked,expected', [
    (True, 'recipient@example.com', 'active', False, True),
    (False, 'recipient@example.com', 'active', False, False),
    (True, None, 'active', False, True),
    (True, 'recipient@example.com', 'banned', False, False),
    (True, 'recipient@example.com', 'active', True, False),
])
def test_message_email_respects_current_recipient_settings_and_blocks(monkeypatch, preference, email, status, blocked, expected):
    import api_utils

    sender = user_from_dynamodb({'id': 'sender', 'name': 'Sender', 'created_at': 1})
    recipient = user_from_dynamodb({'id': 'recipient', 'name': 'Recipient', 'user_email_pk': email,
                                    'created_at': 1, 'status': status, 'notify_direct_messages': preference})
    lookup = Mock(return_value=recipient)
    send = Mock()
    url = Mock(return_value='https://example.com/messages/sender')
    monkeypatch.setattr(service, 'message_peer', lookup)
    monkeypatch.setattr(service, 'can_send_message', lambda *args: not blocked)
    monkeypatch.setattr(service, 'get_url', url)
    monkeypatch.setattr(api_utils, '_send_account_notification', send)
    service._notify_direct_message(sender, recipient.id, "00000000-0000-4000-8000-000000000006")
    lookup.assert_called_once_with(sender, recipient.id)
    assert bool(send.call_count) == expected
    if expected:
        assert send.call_args.args[:2] == (recipient, 'notify_direct_messages')
        assert send.call_args.kwargs['target_url'] == 'https://example.com/messages/sender'
        assert url.call_args.kwargs['user_id'] == sender.id
