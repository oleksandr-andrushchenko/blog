"""Migrate detailed activities into UTC-day/type counters; dry run by default.

Deploy all counter writers and finish old API/worker/CLI invocations before applying.
--writers-ready explicitly acknowledges this prerequisite. --prune-legacy removes
verified event details only after counters are activated. New writes remain enabled.
Example: python scripts/migrate_activity_counters.py --username threads
"""
import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys
import time


BATCH_SIZE = 98  # Source rows + one daily counter + one account guard <= 100 writes.


def _user(table, user_id):
    item = table.get_item(Key={'pk': f'USER#{user_id}', 'sk': 'META'}, ConsistentRead=True).get('Item')
    if not item or item.get('deletion_id'):
        raise ValueError('user is missing or account erasure is pending')
    if int(item.get('activity_v', 0)) not in (0, 1):
        raise ValueError('unsupported activity counter version')
    return item


def _guard(table, user_id):
    return {'ConditionCheck': {'TableName': table.name,
        'Key': {'pk': f'USER#{user_id}', 'sk': 'META'},
        'ConditionExpression': 'attribute_exists(pk) AND attribute_not_exists(deletion_id)'}}


def _rows(table, user_id, prefix):
    from boto3.dynamodb.conditions import Key
    params = {'KeyConditionExpression': Key('pk').eq(f'USER_ACTIVITY#{user_id}') & Key('sk').begins_with(prefix),
              'ConsistentRead': True}
    if prefix == 'ACTIVITY#':
        params.update(ProjectionExpression='pk, sk, event_type, created_at, profile_visible, #m',
                      ExpressionAttributeNames={'#m': 'm'}, Limit=200)
    while True:
        page = table.query(**params)
        yield from page.get('Items', [])
        if not page.get('LastEvaluatedKey'):
            break
        params['ExclusiveStartKey'] = page['LastEvaluatedKey']


def _event(item):
    from activity_counters import ACTIVITY_TYPES, activity_day
    if not item.get('profile_visible'):
        return None
    kind = item.get('event_type')
    if kind not in ACTIVITY_TYPES:
        raise ValueError('unsupported visible legacy activity type; details retained')
    return activity_day(item['created_at']), kind


def _retryable(error):
    code = error.response.get('Error', {}).get('Code')
    if code in {'TransactionConflictException', 'ConditionalCheckFailedException'}:
        return True
    if code != 'TransactionCanceledException':
        return False
    reasons = {reason.get('Code') for reason in error.response.get('CancellationReasons', [])}
    return bool(reasons & {'ConditionalCheckFailed', 'TransactionConflict'})


def _stage(table, user_id, items):
    """Mark each source once and increment historical counters in the same transaction."""
    from activity_counters import LEGACY_COUNTER_PREFIX, activity_counter_key
    from botocore.exceptions import ClientError
    pending = [item for item in items if item.get('m') != 1]
    for attempt in range(5):
        if not pending:
            return
        _user(table, user_id)
        events = [event for item in pending if (event := _event(item))]
        days = {day for day, _ in events}
        if len(days) > 1:
            raise ValueError('stage each UTC day separately')
        counts = Counter(kind for _, kind in events)
        day = next(iter(days), None)
        writes = [_guard(table, user_id)]
        if counts:
            pk, sk = activity_counter_key(user_id, day)
            names = {f'#type{index}': LEGACY_COUNTER_PREFIX + kind for index, kind in enumerate(counts)}
            values = {f':count{index}': count for index, count in enumerate(counts.values())}
            writes.append({'Update': {'TableName': table.name, 'Key': {'pk': pk, 'sk': sk},
                'UpdateExpression': 'ADD ' + ', '.join(f'#type{i} :count{i}' for i in range(len(counts))),
                'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}})
        for item in pending:
            writes.append({'Update': {'TableName': table.name, 'Key': {'pk': item['pk'], 'sk': item['sk']},
                'UpdateExpression': 'SET #m = :one',
                'ConditionExpression': 'attribute_exists(pk) AND attribute_not_exists(#m)',
                'ExpressionAttributeNames': {'#m': 'm'}, 'ExpressionAttributeValues': {':one': 1}}})
        try:
            table.meta.client.transact_write_items(TransactItems=writes)
            return
        except ClientError as error:
            if not _retryable(error) or attempt == 4:
                raise
            _user(table, user_id)
            pending = [current for item in pending if (current := table.get_item(
                Key={'pk': item['pk'], 'sk': item['sk']}, ConsistentRead=True).get('Item')) and current.get('m') != 1]
            time.sleep(.05 * (attempt + 1))


def _prune(table, user_id, items):
    from botocore.exceptions import ClientError
    pending = list(items)
    for attempt in range(5):
        if not pending:
            return
        if _user(table, user_id).get('activity_v') != 1:
            raise ValueError('activate counters before removing legacy details')
        writes = [_guard(table, user_id)]
        for item in pending:
            if item.get('m') != 1:
                raise ValueError('unmigrated legacy activity found; details retained')
            writes.append({'Delete': {'TableName': table.name, 'Key': {'pk': item['pk'], 'sk': item['sk']},
                'ConditionExpression': '#m = :one', 'ExpressionAttributeNames': {'#m': 'm'},
                'ExpressionAttributeValues': {':one': 1}}})
        try:
            table.meta.client.transact_write_items(TransactItems=writes)
            return
        except ClientError as error:
            if not _retryable(error) or attempt == 4:
                raise
            _user(table, user_id)
            pending = [current for item in pending if (current := table.get_item(
                Key={'pk': item['pk'], 'sk': item['sk']}, ConsistentRead=True).get('Item'))]
            time.sleep(.05 * (attempt + 1))


def _merge(table, user_id, key):
    """Fold historical fields into their live counters atomically within one item."""
    from activity_counters import ACTIVITY_TYPES, LEGACY_COUNTER_PREFIX
    from botocore.exceptions import ClientError
    for attempt in range(5):
        _user(table, user_id)
        item = table.get_item(Key=key, ConsistentRead=True).get('Item', {})
        kinds = [kind for kind in ACTIVITY_TYPES if LEGACY_COUNTER_PREFIX + kind in item]
        if not kinds:
            return
        names, values = {}, {':zero': 0}
        sets, removes, conditions = [], [], ['attribute_exists(pk)']
        for index, kind in enumerate(kinds):
            names[f'#live{index}'], names[f'#old{index}'] = kind, LEGACY_COUNTER_PREFIX + kind
            values[f':old{index}'] = item[LEGACY_COUNTER_PREFIX + kind]
            sets.append(f'#live{index} = if_not_exists(#live{index}, :zero) + :old{index}')
            removes.append(f'#old{index}')
            conditions.append(f'#old{index} = :old{index}')
        writes = [_guard(table, user_id), {'Update': {'TableName': table.name, 'Key': key,
            'UpdateExpression': 'SET ' + ', '.join(sets) + ' REMOVE ' + ', '.join(removes),
            'ConditionExpression': ' AND '.join(conditions), 'ExpressionAttributeNames': names,
            'ExpressionAttributeValues': values}}]
        try:
            table.meta.client.transact_write_items(TransactItems=writes)
            return
        except ClientError as error:
            if not _retryable(error) or attempt == 4:
                raise
            time.sleep(.05 * (attempt + 1))


def migrate_user(table, user_id, *, apply=False, prune_legacy=False):
    from activity_counters import ACTIVITY_TYPES, LEGACY_COUNTER_PREFIX
    if prune_legacy and not apply:
        raise ValueError('removing legacy details requires apply=True')
    _user(table, user_id)
    expected, batches = Counter(), defaultdict(list)
    rows = hidden = 0
    # Dry runs validate all inputs and never change records.
    for item in _rows(table, user_id, 'ACTIVITY#'):
        rows += 1
        event = _event(item)
        if event:
            expected[event] += 1
        else:
            hidden += 1
        if apply and item.get('m') != 1:
            group = batches[event[0] if event else None]
            group.append(item)
            if len(group) == BATCH_SIZE:
                _stage(table, user_id, group)
                group.clear()
    if not apply:
        return {'user_id': user_id, 'legacy_rows': rows, 'visible_events': sum(expected.values()),
                'hidden_rows': hidden, 'days': len({day for day, _ in expected}), 'applied': False}
    for group in batches.values():
        if group:
            _stage(table, user_id, group)
    # Another migration may already have activated this user. Source markers and
    # per-item counter merges make reruns safe, including after partial pruning.
    if _user(table, user_id).get('activity_v') != 1:
        expected = Counter()
        for item in _rows(table, user_id, 'ACTIVITY#'):
            if item.get('m') != 1:
                raise ValueError('legacy writes are still running; do not activate counters')
            if event := _event(item):
                expected[event] += 1
        staged = Counter()
        for item in _rows(table, user_id, 'DAY#'):
            for kind in ACTIVITY_TYPES:
                if count := int(item.get(LEGACY_COUNTER_PREFIX + kind, 0)):
                    staged[(item['sk'].removeprefix('DAY#'), kind)] += count
        if staged != expected:
            raise ValueError('historical counters do not match legacy activity; details retained')
        table.update_item(Key={'pk': f'USER#{user_id}', 'sk': 'META'},
                          UpdateExpression='SET activity_v = :version',
                          ExpressionAttributeValues={':version': 1},
                          ConditionExpression='attribute_exists(pk) AND attribute_not_exists(deletion_id)')
    if prune_legacy:
        batch = []
        for item in _rows(table, user_id, 'ACTIVITY#'):
            batch.append(item)
            if len(batch) == BATCH_SIZE:
                _prune(table, user_id, batch)
                batch.clear()
        if batch:
            _prune(table, user_id, batch)
        if next(_rows(table, user_id, 'ACTIVITY#'), None):
            raise ValueError('legacy rows remain; check that old writers are stopped')
        for item in _rows(table, user_id, 'DAY#'):
            _merge(table, user_id, {'pk': item['pk'], 'sk': item['sk']})
    return {'user_id': user_id, 'legacy_rows': rows, 'visible_events': sum(expected.values()),
            'hidden_rows': hidden, 'applied': True, 'details_removed': prune_legacy}


def configure(env_file):
    if os.environ.get('APP_STAGE') != 'prod':
        raise ValueError('Run through scripts-production with production configuration')
    import boto3
    session = boto3.Session()
    if not os.environ.get('DYNAMODB_TABLE'):
        stack = session.client('cloudformation').describe_stacks(StackName=os.environ['AWS_STACK'])['Stacks'][0]
        outputs = {item['OutputKey']: item['OutputValue'] for item in stack['Outputs']}
        os.environ['DYNAMODB_TABLE'] = outputs['DynamoDbTableName']
    return session.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument('--user-id', action='append')
    targets.add_argument('--username', action='append')
    targets.add_argument('--all-users', action='store_true')
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1] / '.env.prod')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--prune-legacy', action='store_true')
    parser.add_argument('--writers-ready', action='store_true', help='Confirm all old API/worker/CLI writers have finished')
    args = parser.parse_args()
    if args.prune_legacy and not args.apply:
        parser.error('--prune-legacy requires --apply')
    if args.apply and not args.writers_ready:
        parser.error('--apply requires --writers-ready after deploying and draining all old writers')
    table = configure(args.env_file)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'shared'))
    from boto3.dynamodb.conditions import Key
    ids = set(args.user_id or [])
    for username in args.username or []:
        result = table.query(IndexName='USERS_BY_USERNAME', KeyConditionExpression=Key('username').eq(username),
                             ProjectionExpression='id')
        if len(result.get('Items', [])) != 1:
            raise ValueError('username does not identify one account')
        ids.add(result['Items'][0]['id'])
    if args.all_users:
        for status in ('active', 'banned'):
            params = {'IndexName': 'USERS_BY_STATUS_RATING',
                      'KeyConditionExpression': Key('user_status_pk').eq('USER#' + status), 'ProjectionExpression': 'id'}
            while True:
                page = table.query(**params)
                ids.update(item['id'] for item in page.get('Items', []))
                if not page.get('LastEvaluatedKey'):
                    break
                params['ExclusiveStartKey'] = page['LastEvaluatedKey']
    for user_id in sorted(ids):
        print(json.dumps(migrate_user(table, user_id, apply=args.apply, prune_legacy=args.prune_legacy), sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
