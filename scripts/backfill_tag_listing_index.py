"""Backfill compact tag-listing metadata; production dry-run by default.

Deploy the index and all API/worker writers first. Pause catalog edits/imports
for a full --apply --activate run; activation verifies the complete GSI contents.
No article records, counters, notifications, or publication dates are changed.
"""

import argparse
import json
import os
from pathlib import Path
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / folder) for folder in ('shared', 'api-lambda')]


def print_record(record):
    print(json.dumps(record, default=str))


def backfill_article(table, article_id, *, apply):
    from shared_utils import add_put_tag_combos_transact, dynamodb_transact_write, article_from_dynamodb

    key = {'pk': f'POST#{article_id}', 'sk': 'META'}
    item = table.get_item(Key=key, ConsistentRead=True).get('Item')
    if not item or item['status'] != 'published':
        return set()
    writes = []
    add_put_tag_combos_transact(writes, article_from_dynamodb(item))
    names, values, conditions = {}, {}, []
    for field in ('status', 'tags', 'rating_sk', 'category', 'created_at'):
        alias = '#' + field
        names[alias] = field
        if field in item:
            token = ':' + field
            values[token] = item[field]
            conditions.append(f'{alias} = {token}')
        else:
            conditions.append(f'attribute_not_exists({alias})')
    writes.append({'ConditionCheck': {
        'TableName': table.name, 'Key': key, 'ConditionExpression': ' AND '.join(conditions),
        'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values,
    }})
    relations = [operation['Put']['Item'] for operation in writes if 'Put' in operation]
    complete = all(
        all(existing.get(key) == value for key, value in relation.items())
        for relation in relations
        for existing in [table.get_item(Key={'pk': relation['pk'], 'sk': relation['sk']},
                                        ConsistentRead=True).get('Item', {})]
    )
    if apply and not complete:
        dynamodb_transact_write(writes)
    print_record({'input': item, 'output': {
        'action': 'skip' if complete else 'backfill-tag-listings', 'applied': apply and not complete,
        'relations': relations,
    }})
    return {(operation['Put']['Item']['pk'], operation['Put']['Item']['sk'],
             operation['Put']['Item']['tag_rating_sk']) for operation in writes if 'Put' in operation}


def activate(table, expected):
    from article_listing_index import TAG_LISTING_READY_KEY, TAG_RATING_INDEX
    from shared_utils import add_dynamodb_put_transact, dynamodb_transact_write

    indexes = table.meta.client.describe_table(TableName=table.name)['Table'].get('GlobalSecondaryIndexes', [])
    if not any(index['IndexName'] == TAG_RATING_INDEX and index['IndexStatus'] == 'ACTIVE'
               for index in indexes):
        raise RuntimeError('Tag popularity index is not ACTIVE')
    indexed = set()
    params = {'IndexName': TAG_RATING_INDEX, 'ProjectionExpression': 'pk, sk, tag_rating_sk', 'Limit': 1000}
    while True:
        page = table.scan(**params)
        indexed.update((item['pk'], item['sk'], item['tag_rating_sk']) for item in page.get('Items', []))
        if not page.get('LastEvaluatedKey'):
            break
        params['ExclusiveStartKey'] = page['LastEvaluatedKey']
    if indexed != expected:
        raise RuntimeError(f'Index verification incomplete: {len(expected - indexed)} missing, '
                           f'{len(indexed - expected)} unexpected. Keep catalog edits paused and rerun; '
                           'GSI propagation may still be in progress.')
    writes = []
    add_dynamodb_put_transact(writes, TAG_LISTING_READY_KEY, {'version': 1})
    dynamodb_transact_write(writes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table-name', help='Explicit DynamoDB table, overriding DYNAMODB_TABLE')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--activate', action='store_true', help='Verify the full index and enable indexed reads')
    parser.add_argument('--limit', type=int, help='Maximum articles to review/backfill; cannot activate a partial run')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    if args.activate and (not args.apply or args.limit is not None):
        parser.error('--activate requires --apply and an unlimited full run')
    if args.table_name:
        os.environ['DYNAMODB_TABLE'] = args.table_name
    os.environ['TELEGRAM_LOG_LEVEL'] = 'OFF'
    try:
        from app_config import get_dynamodb_table_name
        if not get_dynamodb_table_name():
            parser.error('DYNAMODB_TABLE or --table-name is required')
        from boto3.dynamodb.conditions import Key
        from shared_utils import get_dynamodb_table

        table = get_dynamodb_table()
        params = {'IndexName': 'POSTS_BY_STATUS_CREATED_AT_2',
                  'KeyConditionExpression': Key('post_status_pk').eq('POST#published'),
                  'ProjectionExpression': 'id', 'Limit': 100}
        expected, seen, errors = set(), set(), 0
        while True:
            page = table.query(**params)
            for item in page.get('Items', []):
                if item['id'] in seen:
                    continue
                seen.add(item['id'])
                try:
                    expected.update(backfill_article(table, item['id'], apply=args.apply))
                except Exception as error:
                    from botocore.exceptions import BotoCoreError, ClientError
                    from shared_utils import DynamoDBTransactionError
                    # Configuration/permission/provider failures must stop the run.
                    if isinstance(error, (BotoCoreError, ClientError, PermissionError)):
                        raise
                    if isinstance(error, DynamoDBTransactionError) and not error.is_conditional():
                        raise
                    errors += 1
                    print_record({'input': item, 'error': {'type': type(error).__name__, 'reason': str(error)}})
                if args.limit and len(seen) >= args.limit:
                    break
            if (args.limit and len(seen) >= args.limit) or not page.get('LastEvaluatedKey'):
                break
            params['ExclusiveStartKey'] = page['LastEvaluatedKey']
        if args.activate and not errors:
            activate(table, expected)
        print_record({'summary': {'articles': len(seen), 'relations': len(expected),
                                  'applied': args.apply, 'activated': args.activate and not errors, 'errors': errors}})
        return int(bool(errors))
    except Exception as error:
        print_record({'fatal': str(error), 'error_type': type(error).__name__})
        return 1


if __name__ == '__main__':
    sys.exit(main())
