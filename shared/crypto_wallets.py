"""Validated creator-owned wallet destinations and article visibility."""
from creator_link_models import validate_link_ids
from validation import validate_catalog_text


def validate_crypto_wallets(values):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError('crypto_wallets must be a list of at most 20 wallets')
    result, seen = [], set()
    allowed = {'id', 'currency', 'network', 'address', 'instructions', 'show_on_user_page',
               'default_all_articles', 'default_categories'}
    for value in values:
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError('Invalid crypto wallet fields')
        identifier = value.get('id')
        validate_link_ids([identifier], field_name='crypto_wallets')
        if identifier in seen:
            raise ValueError('Crypto wallet IDs must be unique')
        seen.add(identifier)
        wallet = {'id': identifier}
        for name, maximum in (('currency', 50), ('network', 100), ('address', 512), ('instructions', 500)):
            text = validate_catalog_text(value.get(name, '')).strip()
            if len(text) > maximum or (name in ('currency', 'address') and not text):
                raise ValueError(f'Invalid wallet {name}')
            if name == 'address' and any(c.isspace() for c in text):
                raise ValueError('Wallet addresses cannot contain whitespace')
            wallet[name] = text
        for option in ('show_on_user_page', 'default_all_articles'):
            enabled = value.get(option, True)
            if type(enabled) is not bool:
                raise ValueError(f'{option} must be true or false')
            wallet[option] = enabled
        wallet['default_categories'] = validate_link_ids(value.get('default_categories', []), field_name='default_categories')
        result.append(wallet)
    return result
