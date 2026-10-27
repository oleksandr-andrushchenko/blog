"""Exchange Cognito login codes without creating or selecting an application account."""
from functools import lru_cache
import hmac

import httpx
from jose import JWTError, jwt

from app_config import config, is_prod
from shared_utils import (
    CodeExchangeFailedError, InvalidCodeError, InvalidTokenError,
    decode_offset, get_dummy_user_token, user_token_from_jwt_claims,
)


@lru_cache(maxsize=4)
def _signing_keys(issuer):
    response = httpx.get(issuer + '/.well-known/jwks.json', timeout=5)
    response.raise_for_status()
    return response.json()['keys']


def _verified_claims(id_token, *, access_token=None, nonce=None):
    issuer = f'https://cognito-idp.{config["aws_region"]}.amazonaws.com/{config["cognito_user_pool_id"]}'
    try:
        header = jwt.get_unverified_header(id_token)
        keys = _signing_keys(issuer)
        key = next((key for key in keys if key.get('kid') == header.get('kid')), None)
        if key is None:
            _signing_keys.cache_clear()
            key = next((key for key in _signing_keys(issuer) if key.get('kid') == header.get('kid')), None)
        if key is None:
            raise InvalidTokenError('Sign-in could not be verified. Please try again')
        claims = jwt.decode(id_token, key, algorithms=['RS256'], audience=config['cognito_client_id'],
                            issuer=issuer, access_token=access_token,
                            options={'require_exp': True, 'require_iat': True,
                                     'require_sub': True, 'require_aud': True, 'require_iss': True})
        if claims.get('token_use') != 'id':
            raise InvalidTokenError('Sign-in could not be verified. Please try again')
        if nonce is not None and (not isinstance(claims.get('nonce'), str)
                                  or not hmac.compare_digest(claims['nonce'], nonce)):
            raise InvalidTokenError('Sign-in could not be verified. Please try again')
        return claims
    except (JWTError, KeyError, TypeError, ValueError):
        raise InvalidTokenError('Sign-in could not be verified. Please try again') from None


def exchange_login_code(code, callback_url, *, verifier=None, nonce=None):
    if not is_prod():
        try:
            return get_dummy_user_token(**(decode_offset(code) if code else {}))
        except (ValueError, TypeError, UnicodeError):
            raise InvalidCodeError('Invalid code') from None
    if not code:
        raise InvalidCodeError('Sign-in did not return an authorization code. Please try again')
    data = {'grant_type': 'authorization_code', 'client_id': config['cognito_client_id'],
            'code': code, 'redirect_uri': callback_url}
    if verifier:
        data['code_verifier'] = verifier
    try:
        response = httpx.post(f'https://{config["cognito_domain"]}/oauth2/token', data=data,
                              auth=(config['cognito_client_id'], config['cognito_client_secret']), timeout=10)
        if response.status_code != 200:
            raise CodeExchangeFailedError('Sign-in could not be completed. Please try again')
        tokens = response.json()
        id_token = tokens.get('id_token')
        if not isinstance(id_token, str) or not id_token:
            raise InvalidTokenError('Sign-in could not be verified. Please try again')
        claims = _verified_claims(id_token, access_token=tokens.get('access_token'), nonce=nonce)
        return user_token_from_jwt_claims(claims)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        raise CodeExchangeFailedError('Sign-in is temporarily unavailable. Please try again') from None
