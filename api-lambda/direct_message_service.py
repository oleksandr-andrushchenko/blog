"""Private message writes, inbox ordering, and monotonic read markers."""

import hashlib
import random
import time

from direct_messages import (
    can_send_message, message_partition, message_peer, require_message_user, validate_message_key,
)
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    build_dynamodb_put_item_params, dynamodb_transact_write, get_dynamodb_item,
    get_dynamodb_table_name, get_url, get_user_url, logger, utc_now,
)
from web import HTTPException


def _retryable(error):
    response = getattr(error.__context__, "response", {})
    reasons = {item.get("Code") for item in response.get("CancellationReasons", [])} - {None, "None"}
    return (response.get("Error", {}).get("Code") == "TransactionCanceledException"
            and bool(reasons) and reasons <= {"ConditionalCheckFailed", "TransactionConflict"})


def _participant_guards(sender, recipient):
    guards = []
    for actor, target in ((sender, recipient), (recipient, sender)):
        guards.extend([
            {"ConditionCheck": {"TableName": get_dynamodb_table_name(),
                                "Key": {"pk": f"USER#{actor}", "sk": "META"},
                                "ConditionExpression": "attribute_exists(pk) AND #status = :active AND attribute_not_exists(deletion_id)",
                                "ExpressionAttributeNames": {"#status": "status"},
                                "ExpressionAttributeValues": {":active": "active"}}},
            {"ConditionCheck": {"TableName": get_dynamodb_table_name(),
                                "Key": {"pk": f"USER#{actor}", "sk": f"REL#{target}"},
                                "ConditionExpression": "attribute_not_exists(#action) OR #action <> :block",
                                "ExpressionAttributeNames": {"#action": "action"},
                                "ExpressionAttributeValues": {":block": "block"}}},
        ])
    return guards


def send_direct_message(user, peer_id, dto):
    require_message_user(user)
    partition = message_partition(user.id, peer_id)
    fingerprint = hashlib.sha256(dto.text.encode()).hexdigest()

    def acknowledged():
        receipt = get_dynamodb_item(partition, f"RECEIPT#{dto.message_id}", consistent_read=True)
        if receipt is None:
            return None
        if receipt["sender_id"] != user.id or receipt["fingerprint"] != fingerprint:
            raise HTTPException(409, "This message ID was already used for another message")
        return get_dynamodb_item(partition, receipt["message_key"], consistent_read=True)

    for attempt in range(8):
        existing = acknowledged()
        if existing:
            return existing
        peer = message_peer(user, peer_id)
        if not can_send_message(user, peer):
            raise HTTPException(403, "Messaging is unavailable between these accounts")
        previous = get_dynamodb_item(partition, "META", consistent_read=True)
        created_at = max(utc_now(), int(previous["created_at"]) + 1 if previous else 0)
        message_key = f"MESSAGE#{created_at:020d}#{dto.message_id}"
        message = {"message_id": dto.message_id, "sender_id": user.id, "recipient_id": peer.id,
                   "text": dto.text, "created_at": created_at}
        sender_keys = {**(previous.get("sender_keys", {}) if previous else {}), user.id: message_key}
        transacts = _participant_guards(user.id, peer.id)
        add_dynamodb_put_transact(transacts, (partition, message_key), message, new_pk_only=True)
        add_dynamodb_put_transact(transacts, (partition, f"RECEIPT#{dto.message_id}"), {
            "sender_id": user.id, "fingerprint": fingerprint, "message_key": message_key,
        }, new_pk_only=True)
        metadata = build_dynamodb_put_item_params((partition, "META"), {
            "revision": int(previous["revision"]) + 1 if previous else 1,
            "created_at": created_at, "last_message_key": message_key, "sender_keys": sender_keys,
            "participant_ids": [user.id, peer.id],
        }, new_pk_only=previous is None)
        if previous:
            metadata["Put"].update({"ConditionExpression": "revision = :revision",
                                      "ExpressionAttributeValues": {":revision": previous["revision"]}})
        transacts.append(metadata)
        for viewer, other in ((user.id, peer.id), (peer.id, user.id)):
            if previous:
                old_key = f"DM_INBOX#{int(previous['created_at']):020d}#{other}"
                add_dynamodb_delete_transact(transacts, (f"USER#{viewer}", old_key))
            add_dynamodb_put_transact(transacts, (f"USER#{viewer}", f"DM_INBOX#{created_at:020d}#{other}"), {
                "peer_id": other, "sender_id": user.id, "preview": dto.text[:160],
                "last_message_key": message_key, "last_incoming_key": sender_keys.get(other, ""), "created_at": created_at,
            })
        try:
            dynamodb_transact_write(transacts)
        except DynamoDBTransactionError as exc:
            if not _retryable(exc):
                raise
            time.sleep(random.uniform(.01, .04) * (attempt + 1))
            continue
        logger.info("Direct message sent", extra={"context": {
            "url": get_url(None, "direct-conversation", user_id=peer.id, absolute=True),
            "by": get_user_url(None, user, absolute=True), "message_id": dto.message_id,
        }})
        # Follow the existing account-notification flow after the owning transaction commits.
        from api_utils import safe_execute

        safe_execute("Direct message notification", _notify_direct_message, user, peer.id, dto.message_id)
        return {**message, "sk": message_key}
    existing = acknowledged()
    if existing:
        return existing
    raise HTTPException(409, "The conversation changed. Retry this message.")


def _notify_direct_message(sender, recipient_id, message_id):
    from api_utils import _send_account_notification

    recipient = message_peer(sender, recipient_id)
    if not recipient.notify_direct_messages or recipient.status != "active":
        return
    if not can_send_message(sender, recipient):
        return
    _send_account_notification(
        recipient, "notify_direct_messages", "You have a new direct message",
        f"{sender.name} sent you a private message.", sender, None,
        target_url=get_url(None, "direct-conversation", user_id=sender.id, absolute=True),
        event_id=f"direct-message:{sender.id}:{message_id}",
    )


def mark_direct_messages_read(user, peer_id, message_key):
    require_message_user(user)
    validate_message_key(message_key)
    partition = message_partition(user.id, peer_id)
    if get_dynamodb_item(partition, message_key, consistent_read=True) is None:
        raise HTTPException(404, "Message not found")
    from account_guard import add_account_guard
    from shared_utils import add_dynamodb_update_transact

    writes = []
    add_dynamodb_update_transact(writes, (f'USER#{user.id}', f'DM_READ#{peer_id}'),
                                changes={'last_read_key': message_key}, add_updated_at=False)
    update = writes[0]['Update']
    update['ConditionExpression'] = 'attribute_not_exists(last_read_key) OR last_read_key < :key'
    update['ExpressionAttributeValues'][':key'] = message_key
    add_account_guard(writes, user.id)
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as exc:
        if not _retryable(exc):
            raise
        current = get_dynamodb_item(f'USER#{user.id}', 'META', consistent_read=True)
        if not current or current.get('deletion_id') or current.get('status') != 'active':
            raise HTTPException(403, 'An active account is required') from exc
        marker = get_dynamodb_item(f'USER#{user.id}', f'DM_READ#{peer_id}', consistent_read=True)
        if not marker or marker.get('last_read_key', '') < message_key:
            raise HTTPException(409, 'Read state changed. Please try again') from exc
