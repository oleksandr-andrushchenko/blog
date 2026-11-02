"""Resumable account erasure. Receipts contain no identity after completion.

Erasure deliberately scans for reverse references: legacy identities, aliases and
activity records have no owner index. Each page is bounded; each removal and its
counter changes commit together. Never split an existing domain transaction.
"""
import json
import re
import secrets
import time
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key

from app_config import config, is_prod
from shared_utils import (
    logger,
    DynamoDBTransactionError, add_decrease_tags_rating_transact,
    add_dynamodb_article_update_transact,
    add_dynamodb_delete_transact, add_dynamodb_put_transact, add_dynamodb_update_transact,
    add_update_category_published_count_transact, compute_rating_sk, dynamodb_transact_write,
    get_dynamodb_item, get_dynamodb_table, get_url, article_from_dynamodb,
    utc_now,
)
from web import HTTPException

PREFIX = 'ACCOUNT_DELETE#'
QUEUE = 'ACCOUNT_DELETE_QUEUE'
PAGE_SIZE = 25
PHASES = ('articles', 'inventory', 'reactions', 'content', 'references', 'media', 'cache', 'finish')
_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='account-delete')


class _UploadSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {'img', 'video', 'audio', 'source', 'a'}:
            self.sources.extend(value for name, value in attrs
                                if name.lower() in {'src', 'poster', 'href'} and value)


def receipt(code):
    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code):
        return None
    return get_dynamodb_item(PREFIX + code, 'META', consistent_read=True)


def result(code, request):
    return {'url': get_url(request, 'account-deletion-status', code=code, absolute=True),
            'confirmation_code': code}


def request_deletion(user_id, *, dedup_key=None):
    """Persist the outbox and disable sessions atomically, before dispatch."""
    if dedup_key:
        previous = get_dynamodb_item('DELETE_REQUEST#' + dedup_key, 'META', consistent_read=True)
        if previous:
            dispatch(previous['code'])
            return previous['code']
    profile = get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True) if user_id else None
    code = profile.get('deletion_id') if profile else None
    new_request = not code
    writes = []
    if not code:
        code = secrets.token_urlsafe(32)
        state = {'state': 'pending' if profile else 'complete', 'created_at': utc_now()}
        if profile:
            state.update(user_id=user_id, phase='articles', version=uuid4().hex)
            add_dynamodb_update_transact(writes, (f'USER#{user_id}', 'META'),
                                        {'deletion_id': code, 'user_status_pk': None}, add_updated_at=False)
            writes[-1]['Update']['ConditionExpression'] = 'attribute_exists(pk) AND attribute_not_exists(deletion_id)'
        add_dynamodb_put_transact(writes, (PREFIX + code, 'META'), state, new_pk_only=True)
        if profile:
            add_dynamodb_put_transact(writes, (QUEUE, code))
    if dedup_key:
        add_dynamodb_put_transact(writes, ('DELETE_REQUEST#' + dedup_key, 'META'), {'code': code}, new_pk_only=True)
    if writes:
        try:
            dynamodb_transact_write(writes)
        except DynamoDBTransactionError as error:
            if error.is_conditional():
                # Another callback or the owner can request erasure concurrently.
                return request_deletion(user_id, dedup_key=dedup_key)
            raise
    if new_request:
        logger.info('Account deletion requested')
        if not profile:
            logger.info('Account deletion completed')
    try:
        dispatch(code)
    except Exception as error:
        # The durable outbox is recovered by the scheduled worker. Do not undo a
        # successful erasure request or expose callback tokens in logs.
        logger.error('Account deletion dispatch failed', extra={'context': {
            'error_type': type(error).__name__,
        }})
    return code


def request_provider_deletion(provider, subject, issued, dedup):
    previous = get_dynamodb_item('DELETE_REQUEST#' + dedup, 'META', consistent_read=True)
    if previous:
        dispatch(previous['code'])
        return previous['code']
    code = secrets.token_urlsafe(32)
    writes = []
    add_dynamodb_put_transact(writes, (PREFIX + code, 'META'), {
        'state': 'pending', 'phase': 'resolve', 'version': uuid4().hex,
        'provider': provider, 'subject': subject, 'issued_at': issued, 'created_at': utc_now()}, new_pk_only=True)
    add_dynamodb_put_transact(writes, (QUEUE, code))
    add_dynamodb_put_transact(writes, ('DELETE_REQUEST#' + dedup, 'META'), {'code': code}, new_pk_only=True)
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if error.is_conditional():
            return request_provider_deletion(provider, subject, issued, dedup)
        raise
    logger.info('Provider account deletion requested', extra={'context': {
        'provider': provider,
    }})
    try:
        dispatch(code)
    except Exception as error:
        logger.error('Provider deletion dispatch failed', extra={'context': {
            'error_type': type(error).__name__,
        }})
    return code


def _resolve(code, state):
    from provider_deletion_service import _facebook_account, _threads_account_page
    if state['provider'] == 'facebook':
        uid = _facebook_account(state['subject'], state['issued_at'])
    else:
        users, cursor = _threads_account_page(state['subject'], state['issued_at'], state.get('cursor'))
        if state.get('candidate'):
            users.add(state['candidate'])
        if len(users) > 1:
            raise RuntimeError('Provider identity belongs to several accounts; manual review required')
        uid = next(iter(users), None)
        if cursor:
            writes = []
            add_dynamodb_update_transact(writes, (PREFIX + code, 'META'),
                                        {'cursor': cursor, 'candidate': uid, 'version': uuid4().hex},
                                        add_updated_at=False)
            writes[-1]['Update']['ConditionExpression'] = '#version = :version'
            writes[-1]['Update']['ExpressionAttributeNames']['#version'] = 'version'
            writes[-1]['Update']['ExpressionAttributeValues'][':version'] = state['version']
            dynamodb_transact_write(writes)
            return
    target = request_deletion(uid)
    writes = []
    add_dynamodb_put_transact(writes, (PREFIX + code, 'META'), {
        'state': 'linked', 'code': target, 'created_at': state['created_at']})
    writes[-1]['Put'].update(ConditionExpression='#version = :version',
                            ExpressionAttributeNames={'#version': 'version'},
                            ExpressionAttributeValues={':version': state['version']})
    add_dynamodb_delete_transact(writes, (QUEUE, code))
    dynamodb_transact_write(writes)


def delete_own_account(user, actor, payload):
    if user.id != actor.id:
        raise HTTPException(403, 'Only the account owner can delete this account')
    if not isinstance(payload, dict) or payload.get('confirmation') != 'DELETE':
        raise HTTPException(422, 'Type DELETE to confirm account deletion')
    return request_deletion(user.id)


def dispatch(code):
    state = receipt(code)
    if not state or state['state'] == 'complete':
        return
    if state['state'] == 'linked':
        return dispatch(state['code'])
    event = {'task': 'delete-account', 'payload': {'code': code}}
    if config.get('background_function_name'):
        response = boto3.client('lambda').invoke(FunctionName=config['background_function_name'],
                                                InvocationType='Event', Payload=json.dumps(event).encode())
        if response['StatusCode'] != 202:
            raise RuntimeError('Account deletion worker did not accept the request')
    elif config.get('app_stage') == 'local':
        _workers.submit(run_event, event)
    else:
        raise RuntimeError('BACKGROUND_FUNCTION_NAME is required for account deletion')


def _guard_state(writes, code, state):
    from app_config import get_dynamodb_table_name
    writes.append({'ConditionCheck': {
        'TableName': get_dynamodb_table_name(), 'Key': {'pk': PREFIX + code, 'sk': 'META'},
        'ConditionExpression': '#version = :version', 'ExpressionAttributeNames': {'#version': 'version'},
        'ExpressionAttributeValues': {':version': state['version']}}})


def _marker(code, kind, identifier):
    return get_dynamodb_item(PREFIX + code, f'{kind}#{identifier}', consistent_read=True)


def _mark(code, state, kind, identifier):
    writes = []
    add_dynamodb_put_transact(writes, (PREFIX + code, f'{kind}#{identifier}'))
    _guard_state(writes, code, state)
    dynamodb_transact_write(writes)


def _article_id(item):
    if item['pk'].startswith('POST_SHARE#'):
        return item['pk'].removeprefix('POST_SHARE#')
    if item['pk'].startswith('POST#'):
        return item['pk'].removeprefix('POST#')
    if item.get('post_id') or item.get('article_id'):
        return item.get('post_id') or item['article_id']
    activity_url = item.get('entity_url') or ''
    if activity_url.startswith('/articles/'):
        return activity_url.removeprefix('/articles/').split('#', 1)[0].split('?', 1)[0]
    return None


def _owned_article(code, item):
    identifier = _article_id(item)
    return bool(identifier and _marker(code, 'POST', identifier))


def _owned_comment(code, item):
    identifier = item.get('comment_id')
    if item.get('entity_type') == 'comment':
        identifier = item.get('entity_id')
    if item['sk'].startswith('COMMENT#'):
        identifier = item['sk'].removeprefix('COMMENT#')
    return bool(identifier and _marker(code, 'COMMENT', f'{_article_id(item)}#{identifier}'))


def _files(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'content' and isinstance(child, str):
                from urllib.parse import urlsplit
                parser = _UploadSources()
                parser.feed(child)
                for source in parser.sources:
                    try:
                        filename = urlsplit(source).path.rsplit('/', 1)[-1]
                    except ValueError:
                        continue
                    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', filename):
                        yield filename
            if key in ('filename', 'image_filename', 'avatar_filename', 'preview_filename', 'site_img_filename') and isinstance(child, str):
                # Public upload keys are basenames. Never turn arbitrary URLs or
                # untrusted paths into filesystem/S3 deletions.
                if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', child):
                    yield child
            elif isinstance(child, (dict, list)):
                yield from _files(child)
    elif isinstance(value, list):
        for child in value:
            yield from _files(child)


def _redirect_belongs(item, user_id):
    profile = get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True) or {}
    target = item.get('redirect_to')
    visited = set()
    for _ in range(32):
        if not target or target in visited:
            return False
        visited.add(target)
        if target == profile.get('username'):
            return True
        lock = get_dynamodb_item('USER_SLUG#' + target, 'META', consistent_read=True)
        if lock:
            return lock.get('user_id') == user_id
        redirect = get_dynamodb_item('USER_REDIRECT#' + target, 'META', consistent_read=True) or {}
        target = redirect.get('redirect_to')
    return False


def _inventory(code, state, item):
    user_id = state['user_id']
    if item['pk'].startswith('USER_REDIRECT#') and _redirect_belongs(item, user_id):
        _mark(code, state, 'SLUG', item['pk'].removeprefix('USER_REDIRECT#'))
    if item['pk'] == f'USER#{user_id}' and item['sk'] == 'META' and item.get('username'):
        _mark(code, state, 'SLUG', item['username'])
    if item['pk'].startswith('USER_SLUG#') and item.get('user_id') == user_id:
        _mark(code, state, 'SLUG', item['pk'].removeprefix('USER_SLUG#'))
    owned = item.get('user_id') == user_id or item['pk'] == f'USER#{user_id}' or _owned_article(code, item)
    if item['sk'].startswith('COMMENT#') and owned:
        _mark(code, state, 'COMMENT', f"{_article_id(item)}#{item['sk'].removeprefix('COMMENT#')}")
    # Identity snapshots in someone else's comment are not their uploaded media.
    if owned and (item['sk'] == 'META' or item['sk'].startswith('COMMENT#')):
        for filename in set(_files(item)):
            _mark(code, state, 'FILE', filename)


def _user_deltas(writes, deleted_user, user_id, deltas):
    if not user_id or user_id == deleted_user:
        return
    if not get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True):
        return
    from engagement_service import add_statistics_update
    deltas = dict(deltas)
    rating = deltas.pop('rating_sk', None)
    if rating is not None:
        add_dynamodb_update_transact(writes, (f'USER#{user_id}', 'META'),
                                    deltas={'rating_sk': rating})
    add_statistics_update(writes, user_id, deltas)


def _existing_update(writes, pk, sk, deltas):
    item = get_dynamodb_item(pk, sk, consistent_read=True)
    if not item:
        return
    signed = {key: delta for key, delta in deltas.items() if key == 'rating_sk'}
    counts = {key: delta for key, delta in deltas.items() if key != 'rating_sk'}
    add_dynamodb_update_transact(writes, (pk, sk), changes={key: max(0, item.get(key, -delta) + delta)
                                                        for key, delta in counts.items()},
                                deltas=signed, add_updated_at=False)
    operation = writes[-1]['Update']
    conditions = ['attribute_exists(pk)']
    for index, key in enumerate(counts):
        name, value = f'#previous_count{index}', f':previous_count{index}'
        operation['ExpressionAttributeNames'][name] = key
        if key in item:
            operation['ExpressionAttributeValues'][value] = item[key]
            conditions.append(f'{name} = {value}')
        else:
            conditions.append(f'attribute_not_exists({name})')
    operation['ConditionExpression'] = ' AND '.join(conditions)


def _remove(code, state, item, phase):
    """One source row is the idempotency guard for all its side effects."""

    pk, sk = item['pk'], item['sk']
    uid = state['user_id']
    if pk.startswith((PREFIX, 'DELETE_REQUEST#')) or (pk == f'USER#{uid}' and sk == 'META'):
        return
    article_id = _article_id(item)
    own_article = _owned_article(code, item)
    own_comment = _owned_comment(code, item)
    personal = any(item.get(field) == uid for field in (
        'user_id', 'owner_id', 'actor_id', 'actor_user_id', 'target_user_id', 'entity_owner_id'))
    if pk.startswith('USER_REDIRECT#'):
        slug = pk.removeprefix('USER_REDIRECT#')
        personal |= bool(_marker(code, 'SLUG', slug))
    personal |= pk in (f'USER#{uid}', f'USER_ACTIVITY#{uid}') or pk.startswith(f'POST_SLUG#{uid}#')
    if item.get('entity_type') == 'article' and item.get('entity_id'):
        own_article |= bool(_marker(code, 'POST', item['entity_id']))
    is_reaction = sk.startswith(('IMP#', 'COMMENT_IMP#', 'REL#')) or pk.startswith('POST_SHARE#')
    is_content = (pk.startswith('POST#') and (sk == 'META' or sk.startswith('COMMENT#')))
    if phase == 'references' and not (personal or own_article or own_comment):
        for filename in set(_files(item)):
            if _marker(code, 'FILE', filename):
                _mark(code, state, 'KEEP', filename)
    if phase == 'reactions' and not is_reaction:
        return
    if phase == 'content' and not is_content:
        return
    if phase == 'references' and (is_reaction or is_content):
        return
    if not (personal or own_article or own_comment):
        return
    # Directory deletion precedes mapping removal, so failed AWS calls remain retryable.
    if pk.startswith('PROVIDER_USER#'):
        _delete_cognito_identity(pk)
    writes = []
    add_dynamodb_delete_transact(writes, (pk, sk))
    guard = writes[0]['Delete']
    guard['ConditionExpression'] = 'attribute_exists(pk)'
    if 'action' in item:
        guard['ConditionExpression'] += ' AND #action = :action'
        guard['ExpressionAttributeNames'] = {'#action': 'action'}
        guard['ExpressionAttributeValues'] = {':action': item['action']}
    article_item = get_dynamodb_item(f'POST#{article_id}', 'META', consistent_read=True) if article_id else None
    article_owner = article_item.get('user_id') if article_item else None
    action = item.get('action')
    if sk.startswith('IMP#') and action in ('like', 'dislike'):
        field = action + 's_count'
        if not own_article and article_item:
            # Keep tag-listing popularity mirrors in step with the article rating.
            delta = -1 if action == 'like' else 1
            article = article_from_dynamodb(article_item)
            add_dynamodb_article_update_transact(writes, article,
                                                deltas={field: -1, 'rating_sk': compute_rating_sk(delta)},
                                                add_updated_at=False)
        for user_id, direction in ((item.get('user_id'), 'given'), (article_owner, 'received')):
            _user_deltas(writes, uid, user_id, {f'article_{action}s_{direction}_count': -1})
    elif sk.startswith('COMMENT_IMP#') and action in ('like', 'dislike'):
        if not own_article and not own_comment:
            _existing_update(writes, pk, f"COMMENT#{item['comment_id']}", {
                action + 's_count': -1, 'rating_sk': -1 if action == 'like' else 1})
        for user_id, direction in ((item.get('user_id'), 'given'), (item.get('owner_id'), 'received')):
            _user_deltas(writes, uid, user_id, {f'comment_{action}s_{direction}_count': -1})
    elif sk.startswith('REL#') and action in ('follow', 'block'):
        follower = action == 'follow'
        _user_deltas(writes, uid, item.get('user_id'), {'following_count' if follower else 'blocking_count': -1})
        _user_deltas(writes, uid, item.get('target_user_id'), {
            'followers_count' if follower else 'blockers_count': -1,
            'rating_sk': compute_rating_sk(-1 if follower else 1)})
    elif pk.startswith('POST_SHARE#'):
        _remove_share(writes, uid, item, own_article, article_item)
    elif sk.startswith('COMMENT#'):
        if not own_article:
            _existing_update(writes, pk, 'META', {'comments_count': -1})
        _user_deltas(writes, uid, item.get('user_id'), {
            'article_comments_given_count': -1, 'post_comments_count': -1})
        # A self comment changes both counters on one user; merge before adding.
        if article_owner != item.get('user_id'):
            _user_deltas(writes, uid, article_owner, {'article_comments_received_count': -1})
    elif is_content and sk == 'META' and item.get('status') == 'published':
        article = article_from_dynamodb(item)
        now = utc_now()
        add_decrease_tags_rating_transact(writes, article.tags, now)
        add_update_category_published_count_transact(writes, article.category, -1, now)
    _guard_state(writes, code, state)
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if error.is_conditional() and not get_dynamodb_item(pk, sk, consistent_read=True):
            return
        raise


def _remove_share(writes, uid, item, own_article, article_item):
    from engagement_service import _map_update
    channel = item['channel']
    if not own_article and article_item:
        counts = dict(article_item.get('shares_counts') or {})
        counts[channel] = max(0, counts.get(channel, 1) - 1)
        _map_update(writes, (article_item['pk'], 'META'), article_item, {'shares_counts': counts})
    users = {}
    for user_id, field in ((item.get('actor_id'), 'article_shares_given_counts'),
                           (item.get('owner_id'), 'article_shares_received_counts')):
        if user_id and user_id != uid:
            users.setdefault(user_id, []).append(field)
    for user_id, fields in users.items():
        profile = get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True)
        if profile:
            changes = {}
            for field in fields:
                counts = dict(profile.get(field) or {})
                counts[channel] = max(0, counts.get(channel, 1) - 1)
                changes[field] = counts
            _map_update(writes, (profile['pk'], 'META'), profile, changes)


def _delete_cognito_identity(pk):
    if not is_prod():
        return
    _, issuer, sub = pk.split('#', 2)
    pool = config['cognito_user_pool_id']
    if issuer != pool:
        # Legacy/nonconfigured provider keys are application mappings only.
        return
    client = boto3.client('cognito-idp')
    response = client.list_users(UserPoolId=pool, Filter=f'sub = "{sub}"', Limit=2)
    for user in response.get('Users', []):
        try:
            client.admin_user_global_sign_out(UserPoolId=pool, Username=user['Username'])
            client.admin_delete_user(UserPoolId=pool, Username=user['Username'])
        except client.exceptions.UserNotFoundException:
            pass


def _advance(code, state, cursor=None):
    changes = {'version': uuid4().hex, 'cursor': cursor}
    if not cursor:
        changes['phase'] = PHASES[PHASES.index(state['phase']) + 1]
    writes = []
    add_dynamodb_update_transact(writes, (PREFIX + code, 'META'), changes, add_updated_at=False)
    writes[0]['Update'].update(ConditionExpression='#version = :version',
                              ExpressionAttributeNames={**writes[0]['Update']['ExpressionAttributeNames'], '#version': 'version'},
                              ExpressionAttributeValues={**writes[0]['Update']['ExpressionAttributeValues'], ':version': state['version']})
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if not error.is_conditional():
            raise


def _media(code, state):
    params = {'KeyConditionExpression': Key('pk').eq(PREFIX + code) & Key('sk').begins_with('FILE#'),
              'Limit': PAGE_SIZE, 'ConsistentRead': True}
    if state.get('cursor'):
        params['ExclusiveStartKey'] = state['cursor']
    page = get_dynamodb_table().query(**params)
    from account_media_service import erase_upload
    paths = set()
    for item in page.get('Items', []):
        filename = item['sk'].removeprefix('FILE#')
        if not _marker(code, 'KEEP', filename):
            paths.update('/' + key for key in erase_upload(filename))
    if paths and is_prod() and config.get('cloudfront_distribution_id'):
        response = boto3.client('cloudfront').create_invalidation(
            DistributionId=config['cloudfront_distribution_id'], InvalidationBatch={
                'Paths': {'Quantity': len(paths), 'Items': sorted(paths)},
                'CallerReference': 'erase-' + code + '-' + state['version']})
        _mark(code, state, 'CACHE', response['Invalidation']['Id'])
    _advance(code, state, page.get('LastEvaluatedKey'))


def _cache(code, state):
    params = {'KeyConditionExpression': Key('pk').eq(PREFIX + code) & Key('sk').begins_with('CACHE#'),
              'Limit': PAGE_SIZE, 'ConsistentRead': True}
    if state.get('cursor'):
        params['ExclusiveStartKey'] = state['cursor']
    page = get_dynamodb_table().query(**params)
    if page.get('Items'):
        client = boto3.client('cloudfront')
        for item in page['Items']:
            response = client.get_invalidation(DistributionId=config['cloudfront_distribution_id'],
                                               Id=item['sk'].removeprefix('CACHE#'))
            if response['Invalidation']['Status'] != 'Completed':
                # All byte deletions/invalidations have already been queued.
                # Scheduled recovery checks completion without blocking a worker.
                return False
    _advance(code, state, page.get('LastEvaluatedKey'))
    return True


def _start(code, state):
    writes = []
    add_dynamodb_update_transact(writes, (PREFIX + code, 'META'),
                                {'started_at': utc_now()}, add_updated_at=False)
    operation = writes[-1]['Update']
    operation['ConditionExpression'] = '#version = :version AND attribute_not_exists(started_at)'
    operation['ExpressionAttributeNames']['#version'] = 'version'
    operation['ExpressionAttributeValues'][':version'] = state['version']
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if error.is_conditional():
            return
        raise
    logger.info('Account deletion started')


def _finish(code, state):
    # Remove all temporary manifests before scrubbing the last user identifier.
    page = get_dynamodb_table().query(KeyConditionExpression=Key('pk').eq(PREFIX + code),
                                      Limit=PAGE_SIZE, ConsistentRead=True)
    for item in page.get('Items', []):
        if item['sk'] != 'META':
            get_dynamodb_table().delete_item(Key={'pk': item['pk'], 'sk': item['sk']})
    if any(item['sk'] != 'META' for item in page.get('Items', [])):
        return
    writes = []
    add_dynamodb_delete_transact(writes, (f"USER#{state['user_id']}", 'META'))
    add_dynamodb_put_transact(writes, (PREFIX + code, 'META'), {
        'state': 'complete', 'created_at': state['created_at'], 'completed_at': utc_now()})
    writes[-1]['Put'].update(ConditionExpression='#version = :version',
                            ExpressionAttributeNames={'#version': 'version'},
                            ExpressionAttributeValues={':version': state['version']})
    add_dynamodb_delete_transact(writes, (QUEUE, code))
    dynamodb_transact_write(writes)
    logger.info('Account deletion completed')


def run_event(event, *, budget_seconds=600):
    code = (event.get('payload') or {}).get('code')
    if event.get('task') != 'delete-account' or not receipt(code):
        raise ValueError('Invalid account deletion task')
    start = time.monotonic()
    state = None
    try:
        while (state := receipt(code)) and state['state'] == 'pending':
            if time.monotonic() - start >= budget_seconds:
                dispatch(code)
                return
            phase = state['phase']
            if phase != 'resolve' and not state.get('started_at'):
                _start(code, state)
                continue
            if phase == 'resolve':
                _resolve(code, state)
            elif phase == 'media':
                _media(code, state)
            elif phase == 'cache':
                if not _cache(code, state):
                    return
            elif phase == 'finish':
                _finish(code, state)
            else:
                params = {'Limit': PAGE_SIZE, 'ConsistentRead': True}
                if state.get('cursor'):
                    params['ExclusiveStartKey'] = state['cursor']
                page = get_dynamodb_table().scan(**params)
                for item in page.get('Items', []):
                    if phase == 'articles':
                        if item['pk'].startswith('POST#') and item['sk'] == 'META' and item.get('user_id') == state['user_id']:
                            _mark(code, state, 'POST', _article_id(item))
                    elif phase == 'inventory':
                        _inventory(code, state, item)
                    else:
                        _remove(code, state, item, phase)
                _advance(code, state, page.get('LastEvaluatedKey'))
    except Exception as error:
        if not isinstance(error, DynamoDBTransactionError) or not error.is_conditional():
            logger.error('Account deletion failed', extra={'context': {
                'phase': state.get('phase') if state else None,
                'error_type': type(error).__name__,
            }})
        raise


def recover_event(event, *, budget_seconds=600):
    """Scheduled outbox recovery, including failed dispatches and exhausted retries."""
    start = time.monotonic()
    params = {'KeyConditionExpression': Key('pk').eq(QUEUE), 'Limit': PAGE_SIZE, 'ConsistentRead': True}
    if event.get('cursor'):
        params['ExclusiveStartKey'] = event['cursor']
    while True:
        page = get_dynamodb_table().query(**params)
        for item in page.get('Items', []):
            dispatch(item['sk'])
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            return
        if time.monotonic() - start >= budget_seconds:
            _invoke_recovery(cursor)
            return
        params['ExclusiveStartKey'] = cursor


def _invoke_recovery(cursor):
    event = {'task': 'recover-account-deletions', 'cursor': cursor}
    boto3.client('lambda').invoke(FunctionName=config['background_function_name'], InvocationType='Event',
                                 Payload=json.dumps(event).encode())
