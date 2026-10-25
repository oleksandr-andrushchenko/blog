"""Rebuild user engagement totals from durable relations; dry-run by default.

Run through scripts-production with DYNAMODB_TABLE or --table-name configured.
This metadata repair uses guarded writes: replaying reactions would toggle records
and send notifications. Share events are tracked from rollout onward; historical
publication timestamps cannot reconstruct successful-share counts.
"""
import argparse
from functools import lru_cache
import json
import os


def rebuild(user_id, *, apply=False):
    from engagement_service import _map_update
    from shared_utils import dynamodb_transact_write, get_dynamodb_item, get_dynamodb_table

    before = get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True)
    if before is None:
        raise ValueError(f'User not found: {user_id}')
    counts = {'followers_count': 0, 'following_count': 0, 'blockers_count': 0, 'blocking_count': 0,
              'article_comments_given_count': 0, 'article_comments_received_count': 0}
    for resource in ('article', 'comment'):
        for reaction in ('likes', 'dislikes'):
            for direction in ('given', 'received'):
                counts[f'{resource}_{reaction}_{direction}_count'] = 0

    @lru_cache(maxsize=128)
    def article_owner(article_id):
        item = get_dynamodb_item(f'POST#{article_id}', 'META', consistent_read=True)
        return item.get('user_id') if item else None

    cursor = None
    while True:
        page = get_dynamodb_table().scan(ConsistentRead=True, Limit=100,
                                        **({'ExclusiveStartKey': cursor} if cursor else {}))
        for item in page.get('Items', []):
            pk, sk = item['pk'], item['sk']
            actor_id = item.get('user_id')
            if pk.startswith('USER#') and sk.startswith('REL#'):
                actor_id, owner_id = pk[5:], sk[4:]
                if item.get('action') == 'follow':
                    counts['following_count'] += int(actor_id == user_id)
                    counts['followers_count'] += int(owner_id == user_id)
                elif item.get('action') == 'block':
                    counts['blocking_count'] += int(actor_id == user_id)
                    counts['blockers_count'] += int(owner_id == user_id)
            elif pk.startswith('POST#') and sk.startswith(('IMP#', 'COMMENT_IMP#')):
                resource = 'comment' if sk.startswith('COMMENT_IMP#') else 'article'
                owner_id = item.get('owner_id') if resource == 'comment' else article_owner(pk[5:])
                if item.get('action') in ('like', 'dislike') and owner_id:
                    reaction = item['action'] + 's'
                    counts[f'{resource}_{reaction}_given_count'] += int(actor_id == user_id)
                    counts[f'{resource}_{reaction}_received_count'] += int(owner_id == user_id)
            elif pk.startswith('POST#') and sk.startswith('COMMENT#'):
                owner_id = article_owner(pk[5:])
                if owner_id:
                    counts['article_comments_given_count'] += int(actor_id == user_id)
                    counts['article_comments_received_count'] += int(owner_id == user_id)
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            break
    counts['post_comments_count'] = counts['article_comments_given_count']
    changed = any(before.get(field, 0) != value for field, value in counts.items())
    if apply and changed:
        transacts = []
        _map_update(transacts, (f'USER#{user_id}', 'META'), before, counts)
        update = transacts[-1]['Update']
        for field in ('engagement_revision', 'updated_at'):
            alias = '#' + field
            update['ExpressionAttributeNames'][alias] = field
            if field in before:
                update['ExpressionAttributeValues'][':' + field] = before[field]
                update['ConditionExpression'] += f' AND {alias} = :{field}'
            else:
                update['ConditionExpression'] += f' AND attribute_not_exists({alias})'
        dynamodb_transact_write(transacts)
    record = {'input': {'user_id': user_id}, 'output': {
        'action': 'rebuild' if changed else 'skip', 'applied': apply and changed,
        'changes': counts, 'publication_intent': None,
    }}
    print(json.dumps(record))
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', action='append', required=True)
    parser.add_argument('--table-name', help='Explicit DynamoDB table, overriding DYNAMODB_TABLE')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if args.table_name:
        os.environ['DYNAMODB_TABLE'] = args.table_name
    os.environ['TELEGRAM_LOG_LEVEL'] = 'OFF'
    from app_config import get_dynamodb_table_name
    if not get_dynamodb_table_name():
        parser.error('DYNAMODB_TABLE or --table-name is required')
    from botocore.exceptions import BotoCoreError, ClientError
    from shared_utils import NotAuthenticatedError, NotAuthorizedError, UserBannedError
    from engagement_service import _transaction_conflict
    errors = 0
    for user_id in dict.fromkeys(args.user_id):
        try:
            rebuild(user_id, apply=args.apply)
        except (BotoCoreError, PermissionError, NotAuthenticatedError, NotAuthorizedError, UserBannedError):
            raise
        except ClientError as error:
            if not _transaction_conflict(error):
                raise
            errors += 1
            print(json.dumps({'input': {'user_id': user_id}, 'error': {
                'type': type(error).__name__, 'reason': 'Activity changed during the backfill; retry this user',
            }}))
        except Exception as error:
            errors += 1
            print(json.dumps({'input': {'user_id': user_id}, 'error': {
                'type': type(error).__name__, 'reason': str(error),
            }}))
    print(json.dumps({'summary': {'errors': errors, 'users': len(set(args.user_id))}}))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
