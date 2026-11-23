"""Compact, UTC-day activity buckets shared by writers and calendar reads."""
from datetime import datetime, timezone

ACTIVITY_COUNTER_VERSION = 1
ACTIVITY_TIMEZONE = timezone.utc
ACTIVITY_TYPES = ('article.created', 'comment.created')
LEGACY_COUNTER_PREFIX = 'legacy.'


def activity_day(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp) / 1000, ACTIVITY_TIMEZONE).date().isoformat()


def activity_counter_key(user_id: str, day: str) -> tuple[str, str]:
    return f'USER_ACTIVITY#{user_id}', f'DAY#{day}'


def activity_counts(item: dict, *, include_legacy: bool = False) -> dict[str, int]:
    counts = {}
    for kind in ACTIVITY_TYPES:
        count = int(item.get(kind, 0))
        if include_legacy:
            count += int(item.get(LEGACY_COUNTER_PREFIX + kind, 0))
        if count > 0:
            counts[kind] = count
    return counts
