"""Wallet validation accepts plain receiving addresses and rejects ambiguous input."""
import pytest

from crypto_wallets import validate_crypto_wallets
from user_dtos import UpdateUserDTO

WALLET = {'id': 'btc', 'currency': 'BTC', 'network': 'Bitcoin', 'address': 'dummy-not-a-real-address'}

@pytest.mark.parametrize('values', [None, {}, [WALLET, WALLET], [{**WALLET, 'address': 'bad address'}],
    [{**WALLET, 'default_all_articles': 1}], [{**WALLET, 'currency': ''}],
    [{**WALLET, 'url': 'https://example.com'}], [{**WALLET, 'default_categories': 'other'}]])
def test_invalid_wallets_are_rejected(values):
    with pytest.raises(ValueError):
        UpdateUserDTO(crypto_wallets=values)


def test_legacy_options_have_safe_defaults_and_addresses_stay_plain_text():
    wallet = validate_crypto_wallets([WALLET])[0]
    assert wallet['address'] == WALLET['address']
    assert wallet['show_on_user_page'] is True and wallet['default_all_articles'] is True
    assert wallet['default_categories'] == []
