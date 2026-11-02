"""Atomic unread counts, idempotent notifications, and throttled presence writes."""

import hashlib
import random
import re
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from botocore.exceptions import ClientError

from app_config import get_web_base_url
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_put_transact, add_dynamodb_update_transact,
    dynamodb_transact_write, get_dynamodb_item, get_dynamodb_table, utc_now,
)
from site_notifications import (
    NOTIFICATION_ACTION_ICONS, NOTIFICATION_ICONS, PRESENCE_INTERVAL_MS, is_online, notification_icon,
    notification_status, require_notification_user,
)
from validation import validate_catalog_text
from web import HTTPException


def _retryable(error):
    response = getattr(error.__context__, "response", {})
    reasons = {item.get("Code") for item in response.get("CancellationReasons", [])} - {None, "None"}
    return (response.get("Error", {}).get("Code") == "TransactionCanceledException"
            and bool(reasons) and reasons <= {"ConditionalCheckFailed", "TransactionConflict"})


def _target_url(value):
    parts = urlsplit(value)
    origin = urlsplit(get_web_base_url())
    if parts.netloc and (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc):
        raise ValueError("Notification destinations must stay on this website")
    if parts.scheme and not parts.netloc or not parts.path.startswith("/") or parts.path.startswith("//") or "\\" in value:
        raise ValueError("Invalid notification destination")
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment))


def create_site_notification(user, preference, title, message, target_url, *, detail=None, event_id=None, action=None):
    if preference not in NOTIFICATION_ICONS:
        raise ValueError("Unknown notification type")
    if action is not None and action not in NOTIFICATION_ACTION_ICONS.get(preference, {}):
        raise ValueError("Unknown notification action")
    title = validate_catalog_text(title)
    message = validate_catalog_text(message)
    detail = validate_catalog_text(detail) if detail else None
    target_url = _target_url(target_url)
    identifier = hashlib.sha256((event_id or str(uuid4())).encode()).hexdigest()
    partition = f"USER#{user.id}"
    receipt_key = f"SITE_NOTIFICATION_ID#{identifier}"
    for attempt in range(5):
        if get_dynamodb_item(partition, receipt_key, consistent_read=True):
            return {"created": False, "email": False}
        current = get_dynamodb_item(partition, "META", consistent_read=True)
        if not current or current.get("status") != "active" or current.get("deletion_id") or not current.get(preference, True):
            return {"created": False, "email": False}
        now = utc_now()
        online = is_online(current, now)
        email = bool(current.get("user_email_pk")) and not online
        key = f"SITE_NOTIFICATION#{now:020d}#{identifier}"
        notification = {"id": identifier, "title": title, "message": message, "detail": detail,
                        "url": target_url, "type": preference, "action": action,
                        "icon": notification_icon(preference, action), "is_read": False,
                        "delivery": "email" if email else "web", "created_at": now}
        transacts = []
        add_dynamodb_put_transact(transacts, (partition, key), notification, new_pk_only=True)
        add_dynamodb_put_transact(transacts, (partition, receipt_key), {"notification_key": key}, new_pk_only=True)
        add_dynamodb_update_transact(transacts, (partition, "META"),
                                      deltas={"unread_notifications_count": 1, "notifications_revision": 1},
                                      add_updated_at=False)
        update = transacts[-1]["Update"]
        update["ConditionExpression"] = ("attribute_exists(pk) AND #status = :active AND attribute_not_exists(deletion_id) AND "
                                         "(attribute_not_exists(#preference) OR #preference = :enabled) AND ")
        update["ExpressionAttributeNames"].update({"#status": "status", "#preference": preference})
        update["ExpressionAttributeValues"].update({":active": "active", ":enabled": True})
        if "last_seen_at" in current:
            update["ConditionExpression"] += "last_seen_at = :seen"
            update["ExpressionAttributeValues"][":seen"] = current["last_seen_at"]
        else:
            update["ConditionExpression"] += "attribute_not_exists(last_seen_at)"
        try:
            dynamodb_transact_write(transacts)
            return {"created": True, "email": email}
        except DynamoDBTransactionError as exc:
            if not _retryable(exc):
                raise
            time.sleep(random.uniform(.005, .02) * (attempt + 1))
    raise HTTPException(409, "Notification state changed. Please try again")


def record_presence(user):
    require_notification_user(user)
    now = utc_now()
    if user.last_seen_at is not None and now - user.last_seen_at < PRESENCE_INTERVAL_MS:
        return notification_status(user)
    try:
        result = get_dynamodb_table().update_item(
            Key={"pk": f"USER#{user.id}", "sk": "META"},
            UpdateExpression="SET last_seen_at = :now",
            ConditionExpression="attribute_exists(pk) AND #status = :active AND attribute_not_exists(deletion_id) AND "
                                "(attribute_not_exists(last_seen_at) OR last_seen_at <= :expired)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":now": now, ":expired": now - PRESENCE_INTERVAL_MS, ":active": "active"},
            ReturnValues="ALL_NEW",
        )
        return notification_status(result["Attributes"])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        current = get_dynamodb_item(f"USER#{user.id}", "META", consistent_read=True)
        if not current or current.get("status") != "active" or current.get("deletion_id"):
            raise HTTPException(403, "An active account is required") from exc
        return notification_status(current)


def mark_site_notification_read(user, identifier):
    require_notification_user(user)
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise HTTPException(422, "Invalid notification ID")
    partition = f"USER#{user.id}"
    receipt = get_dynamodb_item(partition, f"SITE_NOTIFICATION_ID#{identifier}", consistent_read=True)
    if not receipt:
        raise HTTPException(404, "Notification not found")
    for attempt in range(5):
        item = get_dynamodb_item(partition, receipt["notification_key"], consistent_read=True)
        if not item:
            raise HTTPException(404, "Notification not found")
        if item.get("is_read"):
            break
        transacts = []
        add_dynamodb_update_transact(transacts, (partition, receipt["notification_key"]),
                                      changes={"is_read": True}, add_updated_at=False)
        update = transacts[-1]["Update"]
        update["ConditionExpression"] = "is_read = :unread"
        update["ExpressionAttributeValues"][":unread"] = False
        add_dynamodb_update_transact(transacts, (partition, "META"),
                                      deltas={"unread_notifications_count": -1, "notifications_revision": 1},
                                      add_updated_at=False)
        update = transacts[-1]["Update"]
        update["ConditionExpression"] = "attribute_exists(pk) AND attribute_not_exists(deletion_id) AND unread_notifications_count >= :one"
        update["ExpressionAttributeValues"][":one"] = 1
        try:
            dynamodb_transact_write(transacts)
            break
        except DynamoDBTransactionError as exc:
            if not _retryable(exc):
                raise
            time.sleep(random.uniform(.005, .02) * (attempt + 1))
    else:
        raise HTTPException(409, "Notification state changed. Please try again")
    return notification_status(get_dynamodb_item(partition, "META", consistent_read=True))
