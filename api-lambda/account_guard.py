"""Prevent in-flight mutations from recreating data during account erasure."""
from app_config import get_dynamodb_table_name


def add_account_guard(writes, user_id):
    key = {'pk': f'USER#{user_id}', 'sk': 'META'}
    for write in writes:
        operation = write.get('Update') or write.get('ConditionCheck')
        if operation and operation.get('Key') == key:
            previous = operation.get('ConditionExpression')
            operation['ConditionExpression'] = ((previous + ' AND ') if previous else '') + 'attribute_not_exists(deletion_id)'
            return
    writes.append({'ConditionCheck': {
        'TableName': get_dynamodb_table_name(), 'Key': key,
        'ConditionExpression': 'attribute_exists(pk) AND attribute_not_exists(deletion_id)',
    }})
