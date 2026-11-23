"""Prevent mutations from recreating account data during or after erasure."""


def add_account_guard(writes, user_id):
    from app_config import get_dynamodb_table_name

    key = {'pk': f'USER#{user_id}', 'sk': 'META'}
    for write in writes:
        operation = next(iter(write.values()))
        target = operation.get('Key') or operation.get('Item', {})
        if target.get('pk') == key['pk'] and target.get('sk') == 'META':
            condition = operation.get('ConditionExpression')
            guard = 'attribute_not_exists(deletion_id)'
            if 'Put' not in write:
                guard = 'attribute_exists(pk) AND ' + guard
            if guard not in (condition or ''):
                operation['ConditionExpression'] = f'({condition}) AND {guard}' if condition else guard
            return
    writes.append({'ConditionCheck': {'TableName': get_dynamodb_table_name(), 'Key': key,
        'ConditionExpression': 'attribute_exists(pk) AND attribute_not_exists(deletion_id)'}})
