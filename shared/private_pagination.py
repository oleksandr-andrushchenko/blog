"""Strict participant-scoped cursors for private message and notification histories."""
import re

from query_dtos import decode_query_offset as decode_cursor
from web import HTTPException


def validate_key_cursor(offset, *, partition, sk_pattern):
    if offset is None:
        return
    try:
        cursor = decode_cursor(offset)
        if (set(cursor) != {'pk', 'sk'} or cursor['pk'] != partition
                or not isinstance(cursor['sk'], str) or len(cursor['sk'].encode()) > 1024
                or not re.fullmatch(sk_pattern, cursor['sk'])):
            raise ValueError('offset does not match this private history')
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, 'Invalid private history cursor') from exc
