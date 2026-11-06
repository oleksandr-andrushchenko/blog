"""Populate missing article category names without changing dates, counters, or indexes.

Run through scripts-production. Dry run is the default; --apply performs guarded writes.
Existing names are preserved. Reruns skip completed articles.
"""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared'))


def backfill_article(table, item, category_names, *, apply=False):
    if item.get('category_name'):
        return 'skipped'
    slug = item.get('category', 'other')
    name = category_names.get(slug)
    if not name:
        raise ValueError(f'Unknown category: {slug}')
    key = {'pk': item['pk'], 'sk': item['sk']}
    names = {'#category': 'category', '#name': 'category_name'}
    values = {':name': name}
    if 'category' in item:
        category_guard = '#category = :category'
        values[':category'] = slug
    else:
        category_guard = 'attribute_not_exists(#category)'
    if 'category_name' in item:
        name_guard = '#name = :previous'
        values[':previous'] = item['category_name']
    else:
        name_guard = 'attribute_not_exists(#name)'
    arguments = {
        'Key': key, 'UpdateExpression': 'SET #name = :name',
        'ConditionExpression': f'attribute_exists(pk) AND {category_guard} AND {name_guard}',
        'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values,
    }
    if apply:
        table.update_item(**arguments)
    print(json.dumps({'input': item, 'output': {
        'action': 'save-category-name', 'applied': apply, 'arguments': arguments,
    }}, default=str))
    return 'updated' if apply else 'planned'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table-name', help='Override DYNAMODB_TABLE')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.table_name:
        os.environ['DYNAMODB_TABLE'] = args.table_name
    os.environ['TELEGRAM_LOG_LEVEL'] = 'OFF'
    from app_config import get_dynamodb_table_name
    if not get_dynamodb_table_name():
        parser.error('DYNAMODB_TABLE or --table-name is required')
    from botocore.exceptions import ClientError
    from shared_utils import get_categories, get_dynamodb_table

    table = get_dynamodb_table()
    category_names = {category.slug: category.name for category in get_categories()}
    params = {
        'FilterExpression': 'begins_with(pk, :post) AND sk = :meta',
        'ExpressionAttributeValues': {':post': 'POST#', ':meta': 'META'},
        'ProjectionExpression': 'pk, sk, category, category_name',
        'ConsistentRead': True,
    }
    totals = {'updated': 0, 'planned': 0, 'skipped': 0, 'errors': 0}
    while True:
        page = table.scan(**params)
        for item in page.get('Items', []):
            try:
                result = backfill_article(table, item, category_names, apply=args.apply)
                totals[result] += 1
            except (ValueError, ClientError) as error:
                if isinstance(error, ClientError) and error.response['Error']['Code'] != 'ConditionalCheckFailedException':
                    raise
                totals['errors'] += 1
                print(json.dumps({'input': item, 'output': {
                    'action': 'error', 'type': type(error).__name__, 'reason': str(error),
                }}, default=str))
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            break
        params['ExclusiveStartKey'] = cursor
    print(json.dumps({'summary': totals}))
    return 1 if totals['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
