"""Account-owned welcome delivery, acknowledged only after the modal opens."""
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_update_transact, dynamodb_transact_write,
    get_dynamodb_item, utc_now,
)
from web import HTTPException


def _profile(user):
    profile = get_dynamodb_item(f'USER#{user.id}', 'META', consistent_read=True)
    if not profile or profile.get('deletion_id') or profile.get('status') != 'active':
        raise HTTPException(403, 'An active account is required')
    return profile


def _transition(user, before, *, seen=False):
    writes = []
    changes = {'welcome_state': None, 'welcome_seen_at': utc_now()} if seen else {'welcome_state': 'ready'}
    add_dynamodb_update_transact(writes, (f'USER#{user.id}', 'META'), changes=changes, add_updated_at=False)
    operation = writes[0]['Update']
    operation['ConditionExpression'] = ('attribute_exists(pk) AND attribute_not_exists(deletion_id) '
                                       'AND #status = :active AND #state = :before '
                                       'AND attribute_not_exists(welcome_seen_at)')
    operation['ExpressionAttributeNames'].update({'#status': 'status', '#state': 'welcome_state'})
    operation['ExpressionAttributeValues'].update({':active': 'active', ':before': before})
    try:
        dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if not error.is_conditional():
            raise
        return False
    return True


def ready_welcome(user):
    """Called only after completion succeeds; retries repair an interrupted handoff."""
    profile = _profile(user)
    if profile.get('welcome_state') == 'pending' and not profile.get('welcome_seen_at'):
        _transition(user, 'pending')
        profile = _profile(user)
    return profile.get('welcome_state') == 'ready' and not profile.get('welcome_seen_at')


def acknowledge_welcome(user):
    profile = _profile(user)
    if profile.get('welcome_seen_at'):
        return {'seen': True}
    if profile.get('welcome_state') != 'ready':
        raise HTTPException(409, 'The welcome popup is not ready')
    if not _transition(user, 'ready', seen=True) and not _profile(user).get('welcome_seen_at'):
        raise HTTPException(409, 'The welcome state changed. Please try again')
    return {'seen': True}
