"""Check deployment command expansion using only synthetic configuration."""
import os
from pathlib import Path


ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))


def test_login_link_callback_and_api_code_exchange_configuration():
    from test_apple_signin import template

    resources = template()['Resources']
    callbacks = resources['CognitoUserPoolClient']['Properties']['CallbackURLs']
    assert {'Sub': 'https://api.${DomainName}/auth/login-link/callback'} in callbacks
    environment = resources['ApiFunction']['Properties']['Environment']['Variables']
    assert environment['COGNITO_DOMAIN'] == {'Sub': '${CognitoUserPoolDomain}.auth.${AWS::Region}.amazoncognito.com'}
    assert environment['COGNITO_CLIENT_ID'] == {'Ref': 'CognitoUserPoolClient'}
    assert environment['COGNITO_CLIENT_SECRET'] == {'GetAtt': 'CognitoUserPoolClient.ClientSecret'}
    assert environment['COGNITO_USER_POOL_ID'] == {'Ref': 'CognitoUserPoolV2'}
