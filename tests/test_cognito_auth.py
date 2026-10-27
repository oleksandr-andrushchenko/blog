"""Login proofs verify signature, issuer, client, expiry, and the connection nonce."""
import os
from pathlib import Path
import sys
import time
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwk, jwt

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'shared'))
import cognito_auth as auth
from shared_utils import CodeExchangeFailedError, InvalidTokenError


@pytest.fixture
def exchange(monkeypatch):
    monkeypatch.setattr(auth, 'is_prod', lambda: True)
    for key, value in {'aws_region': 'us-west-2', 'cognito_user_pool_id': 'us-west-2_example',
                       'cognito_domain': 'login.example.com', 'cognito_client_id': 'test-client',
                       'cognito_client_secret': 'test-secret'}.items():
        monkeypatch.setitem(auth.config, key, value)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    public_jwk = {**jwk.construct(public, algorithm='RS256').to_dict(), 'kid': 'test-key'}
    get = Mock(return_value=Mock(json=Mock(return_value={'keys': [public_jwk]})))
    post = Mock()
    monkeypatch.setattr(auth.httpx, 'get', get)
    monkeypatch.setattr(auth.httpx, 'post', post)
    auth._signing_keys.cache_clear()
    claims = {'sub': 'provider-user', 'iss': 'https://cognito-idp.us-west-2.amazonaws.com/us-west-2_example',
              'aud': 'test-client', 'exp': int(time.time()) + 600, 'iat': int(time.time()),
              'token_use': 'id', 'nonce': 'expected-nonce', 'name': 'Creator'}

    def respond(overrides=None, signing_key=private, access_token='test-access-token'):
        token = jwt.encode(claims | (overrides or {}), signing_key, algorithm='RS256',
                           headers={'kid': 'test-key'}, access_token=access_token)
        post.return_value = Mock(status_code=200, json=Mock(return_value={
            'id_token': token, 'access_token': access_token,
        }))

    respond()
    yield respond, post, get, private
    auth._signing_keys.cache_clear()


def test_exchange_sends_pkce_and_verifies_without_creating_a_user(exchange):
    _, post, get, _ = exchange
    token = auth.exchange_login_code('single-use-code', 'https://api.example.com/auth/login-link/callback',
                                     verifier='pkce-verifier', nonce='expected-nonce')
    assert token.sub == 'provider-user' and token.email is None
    assert post.call_args.kwargs['data']['code_verifier'] == 'pkce-verifier'
    assert post.call_args.kwargs['auth'] == ('test-client', 'test-secret')
    get.assert_called_once_with('https://cognito-idp.us-west-2.amazonaws.com/us-west-2_example/.well-known/jwks.json', timeout=5)


def test_normal_sign_in_verifies_id_token_access_token_hash(exchange):
    assert auth.exchange_login_code('code', 'https://web.example.com/login-callback').sub == 'provider-user'


@pytest.mark.parametrize('override', [
    {'aud': 'other-client'}, {'iss': 'https://attacker.example'}, {'nonce': 'other-nonce'},
    {'exp': 1}, {'token_use': 'access'},
])
def test_invalid_login_proofs_are_rejected(exchange, override):
    respond, _, _, _ = exchange
    respond(override)
    with pytest.raises(InvalidTokenError):
        auth.exchange_login_code('code', 'https://api.example.com/callback', nonce='expected-nonce')


def test_wrong_signature_is_rejected(exchange):
    respond, _, _, _ = exchange
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    respond(signing_key=other)
    with pytest.raises(InvalidTokenError):
        auth.exchange_login_code('code', 'https://api.example.com/callback')


@pytest.mark.parametrize('access_token', [None, '', 'different-access-token'])
def test_access_token_must_match_id_token_hash(exchange, access_token):
    _, post, _, _ = exchange
    post.return_value.json.return_value['access_token'] = access_token
    with pytest.raises(InvalidTokenError):
        auth.exchange_login_code('code', 'https://web.example.com/login-callback')


def test_id_token_without_optional_access_token_hash_is_accepted(exchange):
    respond, _, _, _ = exchange
    respond(access_token=None)
    assert auth.exchange_login_code('code', 'https://web.example.com/login-callback').sub == 'provider-user'


def test_code_exchange_failures_do_not_expose_provider_response(exchange):
    _, post, _, _ = exchange
    post.return_value = Mock(status_code=400, text='raw-provider-secret')
    with pytest.raises(CodeExchangeFailedError) as error:
        auth.exchange_login_code('code', 'https://api.example.com/callback')
    assert 'raw-provider-secret' not in str(error.value)
