"""Cached notification counts, delivery selection, and presence throttling."""

import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "api-lambda")]

import site_notification_service as service
from shared_utils import user_from_dynamodb
from site_notifications import NOTIFICATION_ICONS, ONLINE_WINDOW_MS, PRESENCE_INTERVAL_MS, is_online, notification_icon


@pytest.mark.parametrize("seen,status,expected", [(None, "active", False), (1000000, "active", True),
    (1000000 - ONLINE_WINDOW_MS, "active", True), (1000000 - ONLINE_WINDOW_MS - 1, "active", False),
    (1000001, "active", False), (1000000, "banned", False)])
def test_presence_window(seen, status, expected):
    assert is_online({"last_seen_at": seen, "status": status}, 1000000) is expected


def test_fresh_presence_uses_cached_user_state_without_a_write(monkeypatch):
    user = user_from_dynamodb({"id": "user", "name": "User", "created_at": 1, "last_seen_at": 1000000,
                               "unread_notifications_count": 3, "notifications_revision": 7})
    monkeypatch.setattr(service, "utc_now", lambda: 1000000 + PRESENCE_INTERVAL_MS - 1)
    table = Mock()
    monkeypatch.setattr(service, "get_dynamodb_table", lambda: table)
    state = service.record_presence(user)
    assert state["unread_count"] == 3 and state["revision"] == 7
    table.update_item.assert_not_called()


@pytest.mark.parametrize("preference", NOTIFICATION_ICONS)
@pytest.mark.parametrize("seen,email,expected_email", [(1000000, "user@example.com", False),
                                                       (None, "user@example.com", True), (None, None, False)])
def test_delivery_and_counter_writes_are_atomic(monkeypatch, preference, seen, email, expected_email):
    item = {"id": "user", "name": "User", "created_at": 1, "status": "active", "user_email_pk": email}
    if seen is not None:
        item["last_seen_at"] = seen
    user = user_from_dynamodb(item)
    monkeypatch.setattr(service, "get_dynamodb_item", lambda pk, sk, **kwargs: item if sk == "META" else None)
    monkeypatch.setattr(service, "utc_now", lambda: 1000000)
    monkeypatch.setattr(service, "get_web_base_url", lambda: "https://example.com")
    write = Mock()
    monkeypatch.setattr(service, "dynamodb_transact_write", write)
    result = service.create_site_notification(user, preference, "New message", "A private update",
                                             "https://example.com/messages/sender", event_id="message-1")
    assert result == {"created": True, "email": expected_email}
    transactions = write.call_args.args[0]
    assert len(transactions) == 3
    update = transactions[-1]["Update"]
    assert "updated_at" not in update["UpdateExpression"]
    assert update["ExpressionAttributeValues"][":delta_unread_notifications_count"] == 1
    assert update["ExpressionAttributeValues"][":delta_notifications_revision"] == 1
    notification = transactions[0]["Put"]["Item"]
    assert notification["url"] == "/messages/sender"
    assert notification["delivery"] == ("email" if expected_email else "web")
    assert "last_seen_at" in update["ConditionExpression"]


def test_duplicate_notification_does_not_increment_or_email(monkeypatch):
    user = user_from_dynamodb({"id": "user", "name": "User", "created_at": 1})
    monkeypatch.setattr(service, "get_dynamodb_item", lambda *args, **kwargs: {"notification_key": "existing"})
    write = Mock()
    monkeypatch.setattr(service, "dynamodb_transact_write", write)
    result = service.create_site_notification(user, "notify_direct_messages", "Message", "Update", "/messages/sender",
                                             event_id="same-message")
    assert result == {"created": False, "email": False}
    write.assert_not_called()


@pytest.mark.parametrize("preference", NOTIFICATION_ICONS)
def test_disabled_preferences_suppress_both_delivery_channels(monkeypatch, preference):
    item = {"id": "user", "name": "User", "created_at": 1, "status": "active",
            "user_email_pk": "user@example.com", preference: False}
    user = user_from_dynamodb(item)
    monkeypatch.setattr(service, "get_dynamodb_item", lambda pk, sk, **kwargs: item if sk == "META" else None)
    write = Mock()
    monkeypatch.setattr(service, "dynamodb_transact_write", write)
    assert service.create_site_notification(user, preference, "Update", "Private update", "/messages") == {
        "created": False, "email": False,
    }
    write.assert_not_called()


@pytest.mark.parametrize("preference,action,icon", [
    ("notify_article_impressions", "like", "hand-thumbs-up"),
    ("notify_article_impressions", "dislike", "hand-thumbs-down"),
    ("notify_article_status", "published", "journal-check"),
    ("notify_article_status", "rejected", "journal-x"),
    ("notify_article_status", "unpublished", "journal"),
])
def test_notification_icons_follow_event_actions_and_repair_legacy_events(preference, action, icon, monkeypatch):
    item = {"id": "user", "name": "User", "created_at": 1, "status": "active"}
    user = user_from_dynamodb(item)
    monkeypatch.setattr(service, "get_dynamodb_item", lambda pk, sk, **kwargs: item if sk == "META" else None)
    write = Mock()
    monkeypatch.setattr(service, "dynamodb_transact_write", write)
    service.create_site_notification(user, preference, "Update", "Event", "/notifications", action=action)
    row = write.call_args.args[0][0]["Put"]["Item"]
    assert row["icon"] == icon and row["type"] == preference and row["action"] == action
    if preference == "notify_article_status":
        title, message = "Your article status changed", f"Your article “Example” changed from draft to {action}."
    else:
        title = "New reaction to your article"
        message = f"Member {'liked' if action == 'like' else 'disliked'} your article “Example”."
    assert notification_icon(title=title, message=message, fallback=NOTIFICATION_ICONS[preference]) == icon
