"""Propagate saved profile identities in resumable, bounded transactions."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key

from app_config import config
from query_dtos import ArticleStatus
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, get_dynamodb_item, logger,
    query_dynamodb_table,
)

SYNC_KEY = 'PROFILE_SYNC'
PAGE_SIZE = 50
_local_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='profile-sync')


def add_profile_sync(transacts, user_id):
    # This checkpoint and the profile/slug reservation commit together. A newer
    # edit replaces the checkpoint, invalidating every older in-flight page.
    add_dynamodb_put_transact(transacts, (f'USER#{user_id}', SYNC_KEY),
                              {'version': uuid4().hex, 'phase': 0})


def dispatch_profile_sync(user_id):
    if not get_dynamodb_item(f'USER#{user_id}', SYNC_KEY, consistent_read=True):
        return
    event = {'task': 'sync-profile', 'payload': {'user_id': user_id}}
    function_name = config.get('background_function_name')
    if function_name:
        response = boto3.client('lambda').invoke(
            FunctionName=function_name, InvocationType='Event', Payload=json.dumps(event).encode(),
        )
        if response['StatusCode'] != 202:
            raise RuntimeError('Profile worker did not accept the request')
    elif config.get('app_stage') == 'local':
        _local_workers.submit(run_profile_sync_event, event)
    else:
        raise RuntimeError('BACKGROUND_FUNCTION_NAME is required for profile updates')


def _sync_page(user_id, state):
    profile = get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True)
    if profile is None or profile.get('deletion_id'):
        # Erasure owns the profile and its checkpoint from this point onward.
        return False
    statuses = list(ArticleStatus)
    phase = int(state['phase'])
    comments = phase == len(statuses)
    changes = {'user_name': profile.get('name')}
    if comments:
        index = 'POST_COMMENTS_BY_USER_CREATED_AT'
        condition = Key('post_comment_user_pk').eq(f'USER#{user_id}')
        changes.update(user_username=profile.get('username'), user_avatar_filename=profile.get('avatar_filename'))
    else:
        index = 'POSTS_BY_USER_STATUS_CREATED_AT_2'
        condition = Key('post_user_status_pk').eq(f'POST#{user_id}#{statuses[phase]}')
        changes['user_slug'] = profile.get('username')
    page = query_dynamodb_table(index_name=index, key_condition_expr=condition,
                                scan_index_forward=False, limit=PAGE_SIZE,
                                exclusive_start_key=state.get('cursor'))
    cursor = page.get('LastEvaluatedKey')
    finished = comments and not cursor
    transacts = []
    checkpoint_key = (f'USER#{user_id}', SYNC_KEY)
    if finished:
        add_dynamodb_delete_transact(transacts, checkpoint_key)
        guard = transacts[0]['Delete']
    else:
        next_state = {'version': uuid4().hex, 'phase': phase if cursor else phase + 1}
        if cursor:
            next_state['cursor'] = cursor
        add_dynamodb_put_transact(transacts, checkpoint_key, next_state)
        guard = transacts[0]['Put']
    guard.update(ConditionExpression='#version = :version',
                 ExpressionAttributeNames={'#version': 'version'},
                 ExpressionAttributeValues={':version': state['version']})
    for item in page.get('Items', []):
        add_dynamodb_update_transact(transacts, (item['pk'], item['sk']), changes=changes, add_updated_at=False)
    from account_guard import add_account_guard
    add_account_guard(transacts, user_id)
    # Identity snapshots and the cursor advance atomically. This intentionally
    # does not split arbitrary domain transactions, counters, or slug locks.
    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as error:
        if not error.is_conditional():
            raise
        # Another edit or worker advanced the checkpoint. Reload before writing.


def run_profile_sync_event(event, *, budget_seconds=600):
    if not isinstance(event, dict) or event.get('task') != 'sync-profile':
        raise ValueError('Unknown profile sync task')
    payload = event.get('payload')
    user_id = payload.get('user_id') if isinstance(payload, dict) else None
    if not isinstance(user_id, str) or not user_id:
        raise ValueError('Profile sync requires a user_id')
    started = time.monotonic()
    try:
        while state := get_dynamodb_item(f'USER#{user_id}', SYNC_KEY, consistent_read=True):
            if time.monotonic() - started >= budget_seconds:
                dispatch_profile_sync(user_id)
                return
            if _sync_page(user_id, state) is False:
                return
    except Exception:
        logger.exception('Profile sync failed: user=%s', user_id)
        raise
