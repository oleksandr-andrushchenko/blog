"""Paginated related-article discovery using published tag relations."""

import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass

from query_dtos import ArticleStatus, BaseQueryDTO, decode_query_offset
from shared_utils import (
    Key, encode_offset, get_dynamodb_table, article_from_dynamodb, query_dynamodb_table, to_thread,
)


@dataclass(slots=True)
class RelatedArticleQueryDTO(BaseQueryDTO):
    DEFAULT_LIMIT = 10
    limit: int = DEFAULT_LIMIT

    def validate_offset(self):
        # Related listings carry per-tag positions rather than one database key.
        # Full source/tag scoping is checked by _cursor_state after loading the article.
        decode_query_offset(self.offset)

    def __post_init__(self):
        BaseQueryDTO.__post_init__(self)
        if self.limit > BaseQueryDTO.DEFAULT_LIMIT:
            raise ValueError('limit must be between 1 and 40')


def _cursor_state(article, tags, offset):
    if offset is None:
        return {tag: {} for tag in tags}, []
    try:
        if not isinstance(offset, str) or not 1 <= len(offset) <= 32768:
            raise ValueError()
        state = json.loads(base64.b64decode(offset, altchars=b'-_', validate=True).decode())
        if not isinstance(state, dict) or set(state) != {'article_id', 'tags', 'positions', 'pending'}:
            raise ValueError()
        if state['article_id'] != article.id or state['tags'] != tags:
            raise ValueError()
        positions, pending = state['positions'], state['pending']
        if not isinstance(positions, dict) or set(positions) != set(tags):
            raise ValueError()
        for tag, position in positions.items():
            if position is None:
                continue
            if (not isinstance(position, dict) or set(position) != {'pk', 'sk'}
                    or position['pk'] != f'POST_TAG_COMBO#{tag}'
                    or not isinstance(position['sk'], str)
                    or not re.fullmatch(r'POST#[0-9]+#[A-Za-z0-9-]{1,100}', position['sk'])):
                raise ValueError()
        if (not isinstance(pending, list) or len(pending) > len(tags) * (BaseQueryDTO.DEFAULT_LIMIT + 1)
                or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', value)
                       for value in pending) or len(set(pending)) != len(pending)):
            raise ValueError()
        return positions, pending
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ValueError('offset must be a valid cursor for this related-article listing') from error


def _get_article_ids_by_tag(tag, limit, offset=None):
    response = query_dynamodb_table(
        key_condition_expr=Key('pk').eq(f'POST_TAG_COMBO#{tag}'),
        scan_index_forward=False, limit=limit, exclusive_start_key=offset,
    )
    return ([item['post_id'] for item in response.get('Items', []) if item.get('post_id')],
            response.get('LastEvaluatedKey'))


def _get_articles_by_ids(article_ids):
    if not article_ids:
        return []
    table = get_dynamodb_table()
    keys = [{'pk': f'POST#{article_id}', 'sk': 'META'} for article_id in dict.fromkeys(article_ids)]
    items = []
    for start in range(0, len(keys), 100):
        pending_keys = keys[start:start + 100]
        for attempt in range(3):
            response = table.meta.client.batch_get_item(RequestItems={table.name: {'Keys': pending_keys}})
            items.extend(response.get('Responses', {}).get(table.name, []))
            pending_keys = response.get('UnprocessedKeys', {}).get(table.name, {}).get('Keys', [])
            if not pending_keys:
                break
            if attempt < 2:
                time.sleep(0.05 * 2 ** attempt)
        if pending_keys:
            # Advancing a cursor here would permanently skip these candidates.
            raise RuntimeError('Related article batch read left unprocessed keys')
    return [article_from_dynamodb(item) for item in items]


async def get_related_articles(article, limit=10, offset=None):
    limit = RelatedArticleQueryDTO(limit=limit).limit
    tags = sorted(set(article.tags))
    positions, pending = _cursor_state(article, tags, offset)
    if not tags:
        return []

    def matching_tags(candidate):
        if candidate.id == article.id or candidate.status != ArticleStatus.PUBLISHED:
            return []
        return sorted(set(tags).intersection(candidate.tags))

    pool = {candidate.id: candidate for candidate in await to_thread(_get_articles_by_ids, pending)
            if matching_tags(candidate)}
    while len(pool) < limit + 1:
        active_tags = [tag for tag in tags if positions[tag] is not None]
        if not active_tags:
            break
        pages = await asyncio.gather(*(
            to_thread(_get_article_ids_by_tag, tag, limit + 1, positions[tag] or None)
            for tag in active_tags
        ))
        memberships = {}
        for tag, (article_ids, next_key) in zip(active_tags, pages):
            positions[tag] = next_key
            for article_id in article_ids:
                if article_id != article.id:
                    memberships.setdefault(article_id, set()).add(tag)
        candidates = await to_thread(_get_articles_by_ids, list(memberships))
        for candidate in candidates:
            matches = matching_tags(candidate)
            # Each article belongs to its first matching tag's stream. This
            # prevents duplicates even when other tags encounter it much later,
            # without carrying an ever-growing list of seen IDs in the cursor.
            if matches and matches[0] in memberships[candidate.id]:
                pool[candidate.id] = candidate

    ranked = sorted(pool.values(), key=lambda value: (len(matching_tags(value)), value.rating, value.id),
                    reverse=True)
    results = ranked[:limit]
    for candidate in results:
        candidate.offset = None
    remaining = [candidate.id for candidate in ranked[limit:]]
    if results and (remaining or any(position is not None for position in positions.values())):
        results[-1].offset = encode_offset({
            'article_id': article.id, 'tags': tags, 'positions': positions, 'pending': remaining,
        })
    return results
