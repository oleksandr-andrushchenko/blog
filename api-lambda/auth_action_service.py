"""Complete explicit guest actions after a browser-bound sign-in."""
from account_write_guards import add_account_guard
from app_config import get_allowed_origins
from bookmark_service import set_bookmark
from login_flow import consume_continuation, get_continuation, return_url
from article_dtos import UpdateArticleImpressionDTO
from query_dtos import UserStatus
from shared_utils import (
    DynamoDBTransactionError, dynamodb_transact_write, get_dynamodb_item, get_article, tag_subscription_key,
)
from tag_subscription_dtos import TagSubscriptionDTO
from web import HTTPException


def validate_intent(body):
    if not isinstance(body, dict):
        raise HTTPException(422, 'Supply an action')
    action = body.get('action')
    if not isinstance(action, str):
        raise HTTPException(422, 'Supply an action')
    if action in {'like', 'dislike', 'bookmark'}:
        target = body.get('target')
        if not isinstance(target, str) or not target or len(target) > 100 or any(
                char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in target):
            raise HTTPException(422, 'Invalid article')
        return {'action': action, 'target': target}
    if action == 'subscribe':
        tags = body.get('tags')
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise HTTPException(422, 'Supply topics')
        try:
            return {'action': action, 'tags': TagSubscriptionDTO(tags).tags}
        except (TypeError, ValueError):
            raise HTTPException(422, 'Invalid topics') from None
    raise HTTPException(422, 'Unsupported sign-in action')


def require_action_request(request):
    if request.headers.get('origin') not in get_allowed_origins():
        raise HTTPException(403, 'Untrusted request origin')
    if request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise HTTPException(415, 'Use application/json')


def complete_action(user, request, body):
    from api_utils import create_tag_subscription, update_article_impression
    from welcome_service import ready_welcome

    if not isinstance(body, dict) or type(body.get('skip', False)) is not bool:
        raise HTTPException(422, 'Invalid continuation request')
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(403, 'An active account is required')
    pending = get_continuation(user, request)
    if pending.get('done'):
        return {'redirect_url': return_url(pending['return_url']), 'welcome': ready_welcome(user)}
    writes = consume_continuation(user, pending)
    add_account_guard(writes, user.id)
    guard = writes[-1]['ConditionCheck']
    guard['ConditionExpression'] += ' AND #status = :active'
    guard['ExpressionAttributeNames'] = {'#status': 'status'}
    guard['ExpressionAttributeValues'] = {':active': 'active'}
    intent = pending.get('intent', {}) if not body.get('skip') else {}
    action = intent.get('action')
    destination = return_url(pending['return_url'])
    # Mutating services already add an account guard; avoid duplicate transaction keys.
    completion = writes[:1]
    try:
        if action == 'bookmark':
            set_bookmark(user, intent['target'], True, request, completion_writes=completion)
        elif action in {'like', 'dislike'}:
            article = get_article(intent['target'], user)
            update_article_impression(article, UpdateArticleImpressionDTO(action), user, request,
                                     ensure=True, completion_writes=completion)
        elif action == 'subscribe':
            dto = TagSubscriptionDTO(intent['tags'])
            if get_dynamodb_item('ARTICLE_TAG_SUBSCRIBERS#' + tag_subscription_key(dto.tags),
                                 f'USER#{user.id}', consistent_read=True):
                dynamodb_transact_write(writes)
            else:
                create_tag_subscription(dto, user, req=request, completion_writes=completion)
        else:
            dynamodb_transact_write(writes)
    except DynamoDBTransactionError as error:
        if error.is_conditional():
            raise HTTPException(409, 'This action already completed or changed. Refresh to check its state') from None
        raise
    return {'redirect_url': destination, 'welcome': ready_welcome(user)}
