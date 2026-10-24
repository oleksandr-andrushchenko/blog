"""Persist creator-link totals inside saved links and per-article counters."""
from dataclasses import asdict, replace
import random
import time

from botocore.exceptions import ClientError

from creator_links import get_creator_link, get_creator_links
from shared_utils import (
    DynamoDBTransactionError, add_dynamodb_update_transact, dynamodb_transact_write,
    get_dynamodb_item, user_from_dynamodb,
)
from web import HTTPException


def _error_response(error):
    return getattr(error, 'response', None) or getattr(error.__context__, 'response', {})


def _write_transaction(transacts):
    for attempt in range(5):
        try:
            dynamodb_transact_write(transacts)
            return
        except (ClientError, DynamoDBTransactionError) as error:
            response = _error_response(error)
            codes = {reason.get('Code') for reason in response.get('CancellationReasons', [])
                     if reason.get('Code') not in (None, 'None')}
            # Never repeat a write whose completion is uncertain.
            if (response.get('Error', {}).get('Code') != 'TransactionCanceledException'
                    or codes != {'TransactionConflict'} or attempt == 4):
                raise
            time.sleep(random.uniform(0.01, 0.03) * 2 ** attempt)


def _failed_condition(error, index):
    response = _error_response(error)
    reasons = response.get('CancellationReasons', [])
    return (response.get('Error', {}).get('Code') == 'TransactionCanceledException'
            and index < len(reasons) and reasons[index].get('Code') == 'ConditionalCheckFailed'
            and all(reason.get('Code') in (None, 'None') for position, reason in enumerate(reasons)
                    if position != index))


def _read_creator(user_id):
    item = get_dynamodb_item(f'USER#{user_id}', 'META', consistent_read=True)
    if item is None:
        raise HTTPException(404, 'Creator link is unavailable')
    return item, user_from_dynamodb(item)


def _guard_link_lists(update, item, fields):
    update['ConditionExpression'] = 'attribute_exists(pk)'
    for name in fields:
        alias, previous = f'#saved_{name}', f':previous_{name}'
        update['ExpressionAttributeNames'][alias] = name
        if name in item:
            update['ConditionExpression'] += f' AND {alias} = {previous}'
            update['ExpressionAttributeValues'][previous] = item[name]
        else:
            update['ConditionExpression'] += f' AND attribute_not_exists({alias})'
            update['ExpressionAttributeValues'].pop(previous, None)


def _guard_article(update, owner_id):
    update['ConditionExpression'] = 'attribute_exists(pk) AND #click_owner = :click_owner'
    update['ExpressionAttributeNames']['#click_owner'] = 'user_id'
    update['ExpressionAttributeValues'][':click_owner'] = owner_id


def write_user_creator_links(transacts, user, link_changes):
    """Merge read-only counts and guard edited lists against concurrent clicks."""
    index = next(index for index, transaction in enumerate(transacts)
                 if transaction.get('Update', {}).get('Key') == {'pk': f'USER#{user.id}', 'sk': 'META'})
    update = transacts[index]['Update']
    for attempt in range(5):
        item, current = _read_creator(user.id)
        merged = {}
        for name, links in link_changes.items():
            counts = {link.id: link.clicks_count for link in getattr(current, name)}
            merged[name] = [replace(link, clicks_count=counts.get(link.id, 0)) for link in links]
            update['ExpressionAttributeValues'][f':new_{name}'] = [asdict(link) for link in merged[name]]
        _guard_link_lists(update, item, link_changes)
        try:
            _write_transaction(transacts)
        except (ClientError, DynamoDBTransactionError) as error:
            if not _failed_condition(error, index):
                raise
            if attempt == 4:
                raise HTTPException(409, 'Earning links changed while saving. Please try again.') from error
        else:
            for name, links in merged.items():
                setattr(user, name, links)
            return


def record_creator_link_click(user, link_id, article=None, *, kind='support'):
    if kind != 'support':
        raise ValueError('Unsupported creator link kind')
    field = f'{kind}_links'
    article_field = f'{kind}_link_click_counts'
    if article is not None and not getattr(article, article_field):
        initialization = []
        add_dynamodb_update_transact(initialization, (f'POST#{article.id}', 'META'),
                                     changes={article_field: {}}, add_updated_at=False)
        update = initialization[0]['Update']
        update['UpdateExpression'] = (
            f'SET #new_{article_field} = if_not_exists(#new_{article_field}, :new_{article_field})'
        )
        _guard_article(update, user.id)
        _write_transaction(initialization)

    for attempt in range(5):
        _, current = _read_creator(user.id)
        try:
            get_creator_link(current, kind, link_id, article)
        except ValueError as error:
            raise HTTPException(404, 'Creator link is unavailable') from error
        links = get_creator_links(current, kind)
        index = next(index for index, link in enumerate(links) if link.id == link_id)
        transacts = []
        add_dynamodb_update_transact(transacts, (f'USER#{user.id}', 'META'),
                                     deltas={field: 1}, add_updated_at=False)
        update = transacts[0]['Update']
        path = f'#delta_{field}[{index}]'
        update['UpdateExpression'] = (
            f'SET {path}.#clicks = if_not_exists({path}.#clicks, :zero) + :delta_{field}'
        )
        update['ExpressionAttributeNames'].update({'#clicks': 'clicks_count', '#link_id': 'id'})
        update['ExpressionAttributeValues'].update({':zero': 0, ':link_id': link_id})
        update['ConditionExpression'] = f'attribute_exists(pk) AND {path}.#link_id = :link_id'
        if article is not None:
            add_dynamodb_update_transact(transacts, (f'POST#{article.id}', 'META'),
                                         deltas={article_field: 1}, add_updated_at=False)
            update = transacts[-1]['Update']
            update['UpdateExpression'] = (
                f'SET #delta_{article_field}.#link_id = '
                f'if_not_exists(#delta_{article_field}.#link_id, :zero) + :delta_{article_field}'
            )
            update['ExpressionAttributeNames']['#link_id'] = link_id
            update['ExpressionAttributeValues'][':zero'] = 0
            _guard_article(update, user.id)
        try:
            _write_transaction(transacts)
        except (ClientError, DynamoDBTransactionError) as error:
            if not _failed_condition(error, 0):
                raise
            if attempt == 4:
                raise HTTPException(409, 'Earning links changed while recording the click.') from error
        else:
            return
