"""Participant-scoped private message reads used by both Lambdas."""

from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID

from boto3.dynamodb.conditions import Key

from private_pagination import decode_cursor, validate_key_cursor
from shared_utils import get_dynamodb_item, get_dynamodb_table, user_from_dynamodb, encode_offset
from validation import validate_catalog_text
from web import HTTPException

MESSAGE_KEY_PATTERN = r"MESSAGE#[0-9]{20}#[0-9a-f-]{36}"


@dataclass(slots=True)
class DirectMessageDTO:
    text: str
    message_id: str

    def __post_init__(self):
        self.text = validate_catalog_text(self.text).strip()
        if not 1 <= len(self.text) <= 5000:
            raise ValueError("text must contain 1–5000 characters")
        try:
            self.message_id = str(UUID(self.message_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("message_id must be a UUID") from exc


def require_message_user(user):
    if user.status != "active":
        raise HTTPException(403, "An active account is required to use messages")


def message_partition(user_id, peer_id):
    if user_id == peer_id:
        raise HTTPException(422, "Choose another user to message")
    return "DIRECT_MESSAGES#" + hashlib.sha256(json.dumps(sorted([user_id, peer_id])).encode()).hexdigest()


def message_peer(user, peer_id):
    require_message_user(user)
    message_partition(user.id, peer_id)
    item = get_dynamodb_item(f"USER#{peer_id}", "META", consistent_read=True)
    if item is None:
        raise HTTPException(404, "User not found")
    return user_from_dynamodb(item)


def can_send_message(user, peer):
    if user.status != "active" or peer.status != "active" or user.id == peer.id:
        return False
    for actor, target in ((user.id, peer.id), (peer.id, user.id)):
        relation = get_dynamodb_item(f"USER#{actor}", f"REL#{target}", consistent_read=True)
        if relation and relation.get("action") == "block":
            return False
    return True


def message_limit(value):
    try:
        limit = int(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "limit must be an integer") from exc
    if not 1 <= limit <= 40:
        raise HTTPException(422, "limit must be between 1 and 40")
    return limit


def validate_message_key(key):
    if not isinstance(key, str) or not re.fullmatch(MESSAGE_KEY_PATTERN, key):
        raise HTTPException(422, "Invalid message position")


def get_direct_messages(user, peer_id, *, limit=30, offset=None, after=None):
    require_message_user(user)
    partition = message_partition(user.id, peer_id)
    limit = message_limit(limit)
    validate_key_cursor(offset, partition=partition, sk_pattern=MESSAGE_KEY_PATTERN)
    condition = Key("pk").eq(partition)
    if after is not None:
        validate_message_key(after)
        if offset:
            raise HTTPException(422, "Use either offset or after")
        condition &= Key("sk").between(after + "#", "MESSAGE#~")
    else:
        condition &= Key("sk").begins_with("MESSAGE#")
    params = {"KeyConditionExpression": condition, "Limit": limit, "ScanIndexForward": after is not None,
              "ConsistentRead": True}
    if offset:
        params["ExclusiveStartKey"] = decode_cursor(offset)
    response = get_dynamodb_table().query(**params)
    items = response.get("Items", [])
    if after is None:
        items.reverse()
    return {
        "messages": [{"id": item["message_id"], "key": item["sk"], "text": item["text"],
                      "sender_id": item["sender_id"], "created_at": int(item["created_at"])} for item in items],
        "offset": encode_offset(response["LastEvaluatedKey"]) if response.get("LastEvaluatedKey") else None,
    }


def get_direct_conversations(user, *, limit=20, offset=None):
    require_message_user(user)
    partition = f"USER#{user.id}"
    validate_key_cursor(offset, partition=partition, sk_pattern=r"DM_INBOX#[0-9]{20}#[^#]+")
    params = {"KeyConditionExpression": Key("pk").eq(partition) & Key("sk").begins_with("DM_INBOX#"),
              "Limit": message_limit(limit), "ScanIndexForward": False, "ConsistentRead": True}
    if offset:
        params["ExclusiveStartKey"] = decode_cursor(offset)
    response = get_dynamodb_table().query(**params)
    rows = response.get("Items", [])
    receipts = {}
    if rows:
        table = get_dynamodb_table()
        batch = table.meta.client.batch_get_item(RequestItems={table.name: {
            "Keys": [{"pk": partition, "sk": f"DM_READ#{row['peer_id']}"} for row in rows],
            "ConsistentRead": True,
        }})
        if batch.get("UnprocessedKeys", {}).get(table.name, {}).get("Keys"):
            raise RuntimeError("Unable to load all message read markers")
        receipts = {item["sk"]: item for item in batch.get("Responses", {}).get(table.name, [])}
    for row in rows:
        receipt = receipts.get(f"DM_READ#{row['peer_id']}", {})
        row["unread"] = row["last_incoming_key"] > receipt.get("last_read_key", "")
    return {"conversations": rows,
            "offset": encode_offset(response["LastEvaluatedKey"]) if response.get("LastEvaluatedKey") else None}


def get_message_inbox(user, *, limit=20, offset=None):
    page = get_direct_conversations(user, limit=limit, offset=offset)
    rows = page["conversations"]
    if rows:
        table = get_dynamodb_table()
        batch = table.meta.client.batch_get_item(RequestItems={table.name: {
            "Keys": [{"pk": f"USER#{row['peer_id']}", "sk": "META"} for row in rows],
            "ConsistentRead": True,
        }})
        if batch.get("UnprocessedKeys", {}).get(table.name, {}).get("Keys"):
            raise RuntimeError("Unable to load all conversation participants")
        peers = {item["id"]: user_from_dynamodb(item) for item in batch.get("Responses", {}).get(table.name, [])}
        for row in rows:
            row["peer"] = peers.get(row["peer_id"])
    return page
