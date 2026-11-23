"""Reconcile article bookmark totals from durable BOOKMARK pointers.

Dry-run by default; scans are offline maintenance only. Deploy counter-aware
bookmark writers before applying this backfill. Revision guards reject any article
whose bookmarks changed during the scan, including save/remove cycles.
Content, timestamps, publication, identity and other counters are untouched.
"""
import argparse
import json

def bootstrap(*, production=False):
    import os
    if not production or os.environ.get('APP_STAGE') != 'prod':
        raise ValueError('Run through scripts-production with --production')
    import boto3
    if not os.environ.get('DYNAMODB_TABLE'):
        stack = boto3.client('cloudformation').describe_stacks(StackName=os.environ['AWS_STACK'])['Stacks'][0]
        outputs = {item['OutputKey']: item['OutputValue'] for item in stack['Outputs']}
        os.environ['DYNAMODB_TABLE'] = outputs['DynamoDbTableName']
    return boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])


def scan(table, projection):
    params = {'ProjectionExpression': projection, 'ConsistentRead': True, 'Limit': 100}
    while True:
        page = table.scan(**params)
        yield from page.get('Items', [])
        if not page.get('LastEvaluatedKey'):
            return
        params['ExclusiveStartKey'] = page['LastEvaluatedKey']


def rebuild(table, article_ids=None, *, apply=False):
    from botocore.exceptions import ClientError

    if article_ids is None:
        articles = {item['pk'][5:]: item for item in scan(table, 'pk, sk, bookmarks_count, bookmarks_revision')
                   if item['pk'].startswith('POST#') and item['sk'] == 'META'}
    else:
        articles = {}
        for pid in dict.fromkeys(article_ids):
            item = table.get_item(Key={'pk': 'POST#' + pid, 'sk': 'META'}, ConsistentRead=True).get('Item')
            if item:
                articles[pid] = item
    counts = dict.fromkeys(articles, 0)
    for item in scan(table, 'pk, sk'):
        if item['pk'].startswith('USER#') and item['sk'].startswith('BOOKMARK#'):
            pid = item['sk'][9:]
            if pid in counts:
                counts[pid] += 1
    for pid, before in articles.items():
        count = counts[pid]
        if int(before.get('bookmarks_count', 0)) == count:
            continue
        result = 'preview'
        if apply:
            names = {'#count': 'bookmarks_count', '#revision': 'bookmarks_revision'}
            values = {':count': count}
            conditions = ['attribute_exists(pk)']
            for field, alias in [('bookmarks_count', '#count'), ('bookmarks_revision', '#revision')]:
                if field in before:
                    token = ':old_' + field
                    values[token] = before[field]
                    conditions.append(f'{alias} = {token}')
                else:
                    conditions.append(f'attribute_not_exists({alias})')
            try:
                table.update_item(Key={'pk': before['pk'], 'sk': 'META'},
                                  UpdateExpression='SET #count = :count',
                                  ConditionExpression=' AND '.join(conditions),
                                  ExpressionAttributeNames=names, ExpressionAttributeValues=values)
                result = 'applied'
            except ClientError as error:
                if error.response['Error']['Code'] != 'ConditionalCheckFailedException':
                    raise
                result = 'changed_during_scan'
        yield {'article_id': pid, 'before': int(before.get('bookmarks_count', 0)),
               'bookmarks_count': count, 'result': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument('--article-id', action='append')
    targets.add_argument('--all', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    table = bootstrap(production=args.production)
    conflicts = 0
    changed = 0
    for record in rebuild(table, args.article_id, apply=args.apply):
        print(json.dumps(record), flush=True)
        conflicts += record['result'] == 'changed_during_scan'
        changed += 1
    print(json.dumps({'changed': changed, 'conflicts': conflicts, 'applied': args.apply}), flush=True)
    return int(bool(conflicts))


if __name__ == '__main__':
    raise SystemExit(main())
