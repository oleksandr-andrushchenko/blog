"""Private notification reads and approximate presence shared by both Lambdas."""

from boto3.dynamodb.conditions import Key

from private_pagination import decode_cursor, validate_key_cursor
from shared_utils import encode_offset, get_dynamodb_table, utc_now
from web import HTTPException

PRESENCE_INTERVAL_MS = 5 * 60 * 1000
ONLINE_WINDOW_MS = PRESENCE_INTERVAL_MS + 60 * 1000
NOTIFICATION_ICONS = {
    "notify_comment_impressions": "hand-thumbs-up", "notify_article_shares": "share",
    "notify_direct_messages": "chat-dots", "notify_article_comments": "chat-left-text",
    "notify_article_status": "journal-check", "notify_user_impressions": "person-plus",
    "notify_article_impressions": "hand-thumbs-up", "notify_tag_articles": "tags",
}

NOTIFICATION_ACTION_ICONS = {
    "notify_comment_impressions": {"like": "hand-thumbs-up", "dislike": "hand-thumbs-down"},
    "notify_article_impressions": {"like": "hand-thumbs-up", "dislike": "hand-thumbs-down"},
    "notify_article_status": {"published": "journal-check", "rejected": "journal-x", "unpublished": "journal"},
}


def notification_icon(preference=None, action=None, *, title="", message="", fallback="bell"):
    if action is not None:
        return NOTIFICATION_ACTION_ICONS.get(preference, {}).get(action, NOTIFICATION_ICONS.get(preference, fallback))
    # Older records stored only a preference-level icon. Correct recognizable legacy events on reads.
    if title == "New reaction to your article" and " disliked your article “" in message:
        return "hand-thumbs-down"
    if title == "Your article status changed":
        for status, icon in NOTIFICATION_ACTION_ICONS["notify_article_status"].items():
            if message.endswith(f" to {status}."):
                return icon
    return NOTIFICATION_ICONS.get(preference, fallback)


def is_online(user, now=None):
    seen = user.get("last_seen_at") if isinstance(user, dict) else user.last_seen_at
    status = user.get("status", "active") if isinstance(user, dict) else user.status
    age = (utc_now() if now is None else now) - int(seen or 0)
    return status == "active" and bool(seen) and 0 <= age <= ONLINE_WINDOW_MS


def require_notification_user(user):
    if user.status != "active":
        raise HTTPException(403, "An active account is required for notifications")


def notification_status(user):
    get = user.get if isinstance(user, dict) else lambda name, default: getattr(user, name, default)
    return {"user_id": get("id", ""), "unread_count": int(get("unread_notifications_count", 0)),
            "revision": int(get("notifications_revision", 0)),
            "last_seen_at": int(get("last_seen_at", 0) or 0), "presence_interval_ms": PRESENCE_INTERVAL_MS}


def get_site_notifications(user, *, limit=20, offset=None):
    require_notification_user(user)
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "limit must be an integer") from exc
    if not 1 <= limit <= 40:
        raise HTTPException(422, "limit must be between 1 and 40")
    partition = f"USER#{user.id}"
    validate_key_cursor(offset, partition=partition, sk_pattern=r"SITE_NOTIFICATION#[0-9]{20}#[0-9a-f]{64}")
    params = {"KeyConditionExpression": Key("pk").eq(partition) & Key("sk").begins_with("SITE_NOTIFICATION#"),
              "Limit": limit, "ScanIndexForward": False, "ConsistentRead": True}
    if offset:
        params["ExclusiveStartKey"] = decode_cursor(offset)
    response = get_dynamodb_table().query(**params)
    names = ("id", "title", "message", "detail", "url", "icon", "created_at", "is_read", "delivery")
    notifications = []
    for row in response.get("Items", []):
        notification = {name: int(row[name]) if name == "created_at" else row[name] for name in names}
        notification["icon"] = notification_icon(
            row.get("type"), row.get("action"), title=row["title"], message=row["message"],
            fallback=row.get("icon", "bell"),
        )
        notifications.append(notification)
    return {"user_id": user.id, "notifications": notifications,
            "offset": encode_offset(response["LastEvaluatedKey"]) if response.get("LastEvaluatedKey") else None}
