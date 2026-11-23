"""Private bookmark reads shared by web pages and resource API responses."""
import re
import time

from query_dtos import decode_query_offset as decode_cursor
from shared_utils import (
    Key, encode_offset, get_dynamodb_table, get_articles_by_ids, query_dynamodb_table,
)
from web import HTTPException


def require_bookmark_user(user):
    if user.status != 'active':
        raise HTTPException(403, 'An active account is required to use bookmarks')


def bookmark_article_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise HTTPException(422, 'Invalid article ID')
    return value


def bookmark_order_key(created_at, article_id):
    return f'BOOKMARK_AT#{int(created_at):020d}#{article_id}'


def _read_items(keys):
    table = get_dynamodb_table()
    items = []
    for start in range(0, len(keys), 100):
        pending = keys[start:start + 100]
        for attempt in range(3):
            response = table.meta.client.batch_get_item(RequestItems={table.name: {
                'Keys': pending, 'ConsistentRead': True}})
            items.extend(response.get('Responses', {}).get(table.name, []))
            pending = response.get('UnprocessedKeys', {}).get(table.name, {}).get('Keys', [])
            if not pending:
                break
            if attempt < 2:
                time.sleep(0.05 * 2 ** attempt)
        if pending:
            raise RuntimeError('Bookmark read left unprocessed keys')
    return items


def bookmark_states(user, article_ids):
    require_bookmark_user(user)
    if not isinstance(article_ids, list) or len(article_ids) > 100:
        raise HTTPException(422, 'Supply up to 100 article IDs')
    ids = list(dict.fromkeys(bookmark_article_id(value) for value in article_ids))
    items = _read_items([{'pk': f'USER#{user.id}', 'sk': f'BOOKMARK#{value}'} for value in ids])
    saved = {item['sk'].removeprefix('BOOKMARK#') for item in items}
    return {value: value in saved for value in ids}


def list_bookmarks(user, limit=24, offset=None):
    require_bookmark_user(user)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise HTTPException(422, 'Invalid bookmark page size') from None
    if not 1 <= limit <= 40:
        raise HTTPException(422, 'Bookmark page size must be between 1 and 40')
    pk = f'USER#{user.id}'
    cursor = None
    if offset:
        if len(offset) > 2048:
            raise HTTPException(422, 'Invalid bookmark cursor')
        try:
            cursor = decode_cursor(offset)
        except ValueError:
            raise HTTPException(422, 'Invalid bookmark cursor') from None
        if (set(cursor) != {'pk', 'sk'} or cursor['pk'] != pk
                or not isinstance(cursor['sk'], str)
                or not re.fullmatch(r'BOOKMARK_AT#\d{20}#[A-Za-z0-9_-]{1,128}', cursor['sk'])):
            raise HTTPException(422, 'Bookmark cursor does not belong to this account')
    matches = []
    while len(matches) < limit:
        page = query_dynamodb_table(key_condition_expr=Key('pk').eq(pk) & Key('sk').begins_with('BOOKMARK_AT#'),
                                    scan_index_forward=False, limit=limit - len(matches), exclusive_start_key=cursor,
                                    consistent_read=True)
        ids = [item['sk'].rsplit('#', 1)[1] for item in page.get('Items', [])]
        articles = get_articles_by_ids(ids, consistent_read=True)
        owners = {p.user_id for p in articles}
        live_owners = {item['pk'].removeprefix('USER#') for item in _read_items([
            {'pk': f'USER#{uid}', 'sk': 'META'} for uid in sorted(owners)]) if not item.get('deletion_id')}
        matches.extend(p for p in articles if p.status == 'published' and p.user_id in live_owners)
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            break
    return {'articles': matches, 'offset': encode_offset(cursor)}
