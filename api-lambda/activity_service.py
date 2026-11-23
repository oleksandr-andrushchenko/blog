"""API-owned activity writes, appended to the caller's account-guarded transaction."""
from activity_counters import ACTIVITY_TYPES, activity_counter_key, activity_day
from app_config import config


def add_activity_counter(writes: list, user_id: str, kind: str, timestamp: int) -> None:
    if kind not in ACTIVITY_TYPES:
        raise ValueError('unsupported activity type')
    pk, sk = activity_counter_key(user_id, activity_day(timestamp))
    writes.append({'Update': {
        'TableName': config['dynamodb_table'], 'Key': {'pk': pk, 'sk': sk},
        'UpdateExpression': 'ADD #activity :one',
        'ExpressionAttributeNames': {'#activity': kind},
        'ExpressionAttributeValues': {':one': 1},
    }})
