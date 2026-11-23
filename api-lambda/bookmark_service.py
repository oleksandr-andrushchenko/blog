"""Idempotent bookmarks with atomic article totals and unchanged content timestamps."""
from account_write_guards import add_account_guard
from bookmarks import bookmark_order_key, bookmark_article_id, require_bookmark_user
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_delete_transact, add_dynamodb_put_transact,
    dynamodb_transact_write, get_dynamodb_item, get_url, get_user_url, logger, utc_now,
    add_dynamodb_update_transact,
    article_from_dynamodb, user_from_dynamodb,
)
from web import HTTPException


def add_bookmark_count_update(writes, article, delta, *, require_public=False):
    """Guard the count and revision so concurrent saves and backfills cannot drift."""
    count = max(0, int(article.get('bookmarks_count', 0)) + delta)
    add_dynamodb_update_transact(writes, (article['pk'], 'META'),
                                changes={'bookmarks_count': count},
                                deltas={'bookmarks_revision': 1}, add_updated_at=False)
    update = writes[-1]['Update']
    conditions = ['attribute_exists(pk)']
    for field in ('bookmarks_count', 'bookmarks_revision'):
        alias, value = '#' + field, ':previous_' + field
        update['ExpressionAttributeNames'][alias] = field
        if field in article:
            conditions.append(f'{alias} = {value}')
            update['ExpressionAttributeValues'][value] = article[field]
        else:
            conditions.append(f'attribute_not_exists({alias})')
    if require_public:
        conditions.append('#status = :published AND user_id = :owner')
        update['ExpressionAttributeNames']['#status'] = 'status'
        update['ExpressionAttributeValues'].update({':published': 'published', ':owner': article['user_id']})
    update['ConditionExpression'] = ' AND '.join(conditions)
    return count


def set_bookmark(user, article_id, saved, request, *, completion_writes=()):
    require_bookmark_user(user)
    bookmark_article_id(article_id)
    if type(saved) is not bool:
        raise HTTPException(422, 'saved must be true or false')
    key = (f'USER#{user.id}', f'BOOKMARK#{article_id}')
    for attempt in range(4):
        previous = get_dynamodb_item(*key, consistent_read=True)
        article = get_dynamodb_item(f'POST#{article_id}', 'META', consistent_read=True)
        count = int((article or {}).get('bookmarks_count', 0))
        if bool(previous) == saved:
            if completion_writes:
                writes = list(completion_writes)
                add_account_guard(writes, user.id)
                guard = writes[-1]['ConditionCheck']
                guard['ConditionExpression'] += ' AND #status = :active'
                guard['ExpressionAttributeNames'] = {'#status': 'status'}
                guard['ExpressionAttributeValues'] = {':active': 'active'}
                dynamodb_transact_write(writes)
            return {'saved': saved, 'bookmarks_count': count}
        writes = []
        add_account_guard(writes, user.id)
        guard = writes[0]['ConditionCheck']
        guard['ConditionExpression'] += ' AND #status = :active'
        guard['ExpressionAttributeNames'] = {'#status': 'status'}
        guard['ExpressionAttributeValues'] = {':active': 'active'}
        writes.extend(completion_writes)
        if saved:
            if not article or article.get('status') != 'published':
                raise HTTPException(404, 'Article is no longer public')
            owner = get_dynamodb_item(f"USER#{article['user_id']}", 'META', consistent_read=True)
            if not owner or owner.get('deletion_id'):
                raise HTTPException(404, 'Article is no longer public')
            if article['user_id'] != user.id:
                add_account_guard(writes, article['user_id'])
            count = add_bookmark_count_update(writes, article, 1, require_public=True)
            created_at = utc_now()
            add_dynamodb_put_transact(writes, key, {'created_at': created_at}, new_pk_only=True)
            add_dynamodb_put_transact(writes, (key[0], bookmark_order_key(created_at, article_id)), new_pk_only=True)
        else:
            if article:
                count = add_bookmark_count_update(writes, article, -1)
            add_dynamodb_delete_transact(writes, key)
            writes[-1]['Delete'].update(ConditionExpression='created_at = :previous',
                                       ExpressionAttributeValues={':previous': previous['created_at']})
            add_dynamodb_delete_transact(writes, (key[0], bookmark_order_key(previous['created_at'], article_id)))
        try:
            dynamodb_transact_write(writes)
        except DynamoDBTransactionError as error:
            if error.is_conditional():
                if attempt < 3:
                    continue
                raise HTTPException(409, 'Bookmark changed. Refresh and try again') from None
            raise
        logger.info('Article bookmarked' if saved else 'Bookmark removed', extra={'context': {
            'url': get_url(request, 'article', article_id=article_id, absolute=True),
            'by': get_user_url(request, user, absolute=True)}})
        if saved and article['user_id'] != user.id:
            from api_utils import safe_execute

            safe_execute('Article bookmark notification', _notify_bookmark,
                         owner, article, user, request)
        return {'saved': saved, 'bookmarks_count': count}


def _notify_bookmark(owner, article, actor, request):
    from api_utils import _send_account_notification

    _send_account_notification(
        user_from_dynamodb(owner), 'notify_article_bookmarks', 'Your article was bookmarked',
        f'Someone bookmarked your article “{article["title"]}”', article_from_dynamodb(article), request,
        event_id=f'bookmark:{article["id"]}:{int(article.get("bookmarks_revision", 0)) + 1}',
        actor_id=actor.id,
    )
