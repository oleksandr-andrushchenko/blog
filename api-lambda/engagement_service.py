"""API-owned engagement counters and idempotent share tracking."""

from hashlib import sha256
import uuid
import random
import time

from shared_utils import (
    DynamoDBTransactionError, UserStatus, add_dynamodb_put_transact,
    add_dynamodb_update_transact, dynamodb_transact_write, get_dynamodb_item,
    get_article_url, get_user, logger, utc_now,
)
from web import HTTPException
from botocore.exceptions import ClientError

SHARE_CHANNELS = {
    'telegram_publish', 'threads_publish', 'twitter_share_click',
    'facebook_share_click', 'linkedin_share_click', 'email_share_click',
}



def add_statistics_update(transacts, user_id, deltas, *, baselines=None):
    """Atomically adjust counters, including records awaiting the historical backfill.

    The revision lets the maintenance backfill detect concurrent activity. Legacy
    counters missing during removal start at that relation's prior contribution;
    the backfill supplies complete totals from durable relation records.
    """
    key = {'pk': f'USER#{user_id}', 'sk': 'META'}
    update = next((value['Update'] for value in transacts
                   if value.get('Update', {}).get('Key') == key), None)
    if update is None:
        add_dynamodb_update_transact(transacts, (key['pk'], key['sk']),
                                     deltas={'engagement_revision': 1}, add_updated_at=False)
        update = transacts[-1]['Update']
    else:
        update['ExpressionAttributeNames']['#engagement_revision'] = 'engagement_revision'
        update['ExpressionAttributeValues'][':engagement_revision'] = 1
        expression = update['UpdateExpression']
        update['UpdateExpression'] += (', ' if ' ADD ' in expression else ' ADD ')
        update['UpdateExpression'] += '#engagement_revision :engagement_revision'
    names = update['ExpressionAttributeNames']
    values = update['ExpressionAttributeValues']
    expressions = []
    conditions = ['attribute_exists(pk)']
    snapshot = (get_dynamodb_item(key['pk'], key['sk'], consistent_read=True) or {}
                if any(delta < 0 for delta in deltas.values()) else {})
    for index, (field, delta) in enumerate(deltas.items()):
        names[f'#stat{index}'] = field
        if delta < 0 and field in snapshot and snapshot[field] < -delta:
            # A legacy account can have more old reactions than tracked counters.
            # Floor that partial total without overwriting a concurrent increment.
            values[f':previous_stat{index}'] = snapshot[field]
            values[f':floor{index}'] = 0
            expressions.append(f'#stat{index} = :floor{index}')
            conditions.append(f'#stat{index} = :previous_stat{index}')
            continue
        values[f':base{index}'] = (baselines or {}).get(field, max(0, -delta))
        values[f':change{index}'] = delta
        expressions.append(f'#stat{index} = if_not_exists(#stat{index}, :base{index}) + :change{index}')
    expression = update['UpdateExpression']
    if expression.startswith('SET '):
        before, separator, after = expression.partition(' ADD ')
        update['UpdateExpression'] = before + ', ' + ', '.join(expressions) + separator + after
    else:
        update['UpdateExpression'] = 'SET ' + ', '.join(expressions) + ' ' + expression
    update['ConditionExpression'] = ' AND '.join(conditions)


def guard_reaction(transacts, key, current_action):
    """Prevent duplicate requests from applying the same transition twice."""
    if current_action is None:
        return  # New relation puts already require attribute_not_exists(pk).
    for transaction in transacts:
        operation = transaction.get('Update') or transaction.get('Delete')
        if operation and operation['Key'] == {'pk': key[0], 'sk': key[1]}:
            operation['ConditionExpression'] = '#previous_action = :previous_action'
            operation.setdefault('ExpressionAttributeNames', {})['#previous_action'] = 'action'
            operation.setdefault('ExpressionAttributeValues', {})[':previous_action'] = current_action
            return


def _transaction_conflict(error):
    response = getattr(error, 'response', None) or getattr(error.__context__, 'response', {})
    reasons = {reason.get('Code') for reason in response.get('CancellationReasons', [])
               if reason and reason.get('Code') not in (None, 'None')}
    return getattr(error, 'is_conditional', lambda: False)() or bool(reasons) and reasons <= {'ConditionalCheckFailed', 'TransactionConflict'}


def write_reaction(transacts, *, writer=None):
    try:
        (writer or dynamodb_transact_write)(transacts)
    except (DynamoDBTransactionError, ClientError) as error:
        if _transaction_conflict(error):
            raise HTTPException(409, 'This reaction changed. Refresh the page and try again') from None
        raise


def _map_update(transacts, key, item, changes):
    add_dynamodb_update_transact(transacts, key, changes=changes, add_updated_at=False)
    update = transacts[-1]['Update']
    conditions = ['attribute_exists(pk)']
    for index, field in enumerate(changes):
        name = f'#previous{index}'
        update['ExpressionAttributeNames'][name] = field
        if field in item:
            value = f':previous{index}'
            update['ExpressionAttributeValues'][value] = item[field]
            conditions.append(f'{name} = {value}')
        else:
            conditions.append(f'attribute_not_exists({name})')
    update['ConditionExpression'] = ' AND '.join(conditions)


def record_article_share(article, actor, channel, event_id, req=None, *, article_url=None, post_url=None,
                        published_at=None):
    """Count a confirmed publish or a share-control click once across retries."""
    if channel not in SHARE_CHANNELS:
        raise HTTPException(422, 'Unsupported sharing channel')
    if actor is not None and actor.status != UserStatus.ACTIVE:
        raise HTTPException(403, 'An active account is required to share articles')
    event_hash = sha256(f'{channel}:{actor.id if actor else "guest"}:{event_id}'.encode()).hexdigest()
    event_key = (f'POST_SHARE#{article.id}', event_hash)
    for attempt in range(4):
        if get_dynamodb_item(*event_key, consistent_read=True):
            return False
        article_item = get_dynamodb_item(f'POST#{article.id}', 'META', consistent_read=True)
        if not article_item or article_item['status'] != 'published':
            raise HTTPException(404, 'This article is no longer available')
        owner_id = article_item['user_id']
        user_ids = {owner_id} | ({actor.id} if actor else set())
        users = {user_id: get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True)
                 for user_id in user_ids}
        if any(item is None for item in users.values()):
            raise HTTPException(404, 'This account is no longer available')
        transacts = []
        add_dynamodb_put_transact(transacts, event_key, {
            'article_id': article.id, 'owner_id': owner_id,
            'actor_id': actor.id if actor else None, 'channel': channel,
            'created_at': utc_now(),
        }, new_pk_only=True)
        counts = dict(article_item.get('shares_counts') or {})
        counts[channel] = counts.get(channel, 0) + 1
        changes = {'shares_counts': counts}
        if published_at is not None:
            changes[channel.split('_')[0] + '_shared_at'] = published_at
        _map_update(transacts, (f'POST#{article.id}', 'META'), article_item, changes)
        update = transacts[-1]['Update']
        update['ConditionExpression'] += ' AND #share_status = :share_status AND #share_owner = :share_owner'
        update['ExpressionAttributeNames'].update({'#share_status': 'status', '#share_owner': 'user_id'})
        update['ExpressionAttributeValues'].update({':share_status': 'published', ':share_owner': owner_id})
        for user_id, item in users.items():
            changes = {}
            for field, applies in (
                ('article_shares_received_counts', user_id == owner_id),
                ('article_shares_given_counts', actor is not None and user_id == actor.id),
            ):
                if applies:
                    value = dict(item.get(field) or {})
                    value[channel] = value.get(channel, 0) + 1
                    changes[field] = value
            _map_update(transacts, (f'USER#{user_id}', 'META'), item, changes)
        try:
            dynamodb_transact_write(transacts)
        except (DynamoDBTransactionError, ClientError) as error:
            if _transaction_conflict(error) and attempt < 3:
                time.sleep(random.uniform(0.01, 0.03) * 2 ** attempt)
                continue
            raise
        break
    article_url = article_url or get_article_url(req, article, absolute=True)
    logger.info('Article shared to %s' if channel.endswith('_publish') else 'Article sharing control clicked: %s',
                channel.split('_')[0].title(),
                extra={'context': {'article_id': article.id, 'article_url': article_url, 'post_url': post_url,
                                   'owner_id': owner_id, 'actor_id': actor.id if actor else None,
                                   'channel': channel, 'event_id': event_hash,
                                   'reshare': bool(getattr(article, channel.split('_')[0] + '_shared_at', None))}})
    if actor is None or actor.id != owner_id:
        from api_utils import safe_execute
        safe_execute('Article share email', _notify_share, owner_id, actor, article, channel, req, article_url)
    return True


def _notify_share(owner_id, actor, article, channel, req, article_url):
    # Lazy import avoids a cycle with api_utils and keeps email delivery best-effort.
    from api_utils import _send_account_notification
    owner = get_user(owner_id)
    name = actor.name if actor else 'Someone'
    platform = channel.split('_')[0].title()
    message = (f'{name} shared your article “{article.title}” to {platform}.' if channel.endswith('_publish')
               else f'{name} opened {platform} sharing for your article “{article.title}”.')
    _send_account_notification(owner, 'notify_article_shares', 'Your article was shared', message,
                               article, req, target_url=article_url)


def validate_share_click(payload):
    if (not isinstance(payload, dict) or not isinstance(payload.get('channel'), str)
            or payload['channel'] not in SHARE_CHANNELS - {
        'telegram_publish', 'threads_publish',
    }):
        raise HTTPException(422, 'Invalid sharing channel')
    try:
        event_id = str(uuid.UUID(payload['event_id']))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(422, 'Invalid sharing event') from None
    return payload['channel'], event_id
