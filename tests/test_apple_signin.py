"""Optional Cognito Apple configuration resolves credentials outside application Lambdas."""
import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))


class CloudFormationLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    value = loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else loader.construct_sequence(node)
    return {tag: value}


CloudFormationLoader.add_multi_constructor('!', intrinsic)


def template():
    return yaml.load((ROOT / 'cf.yml').read_text(), Loader=CloudFormationLoader)


@pytest.mark.parametrize('configured', [False, True])
def test_apple_provider_is_optional_and_has_a_conditional_dependency(configured):
    data = template()
    parameters, resources = data['Parameters'], data['Resources']
    assert parameters['AppleCredentialsSecretArn']['Default'] == ''
    assert data['Conditions']['AppleSignInEnabled'] == {'Fn::Not': [
        {'Fn::Equals': [{'Ref': 'AppleCredentialsSecretArn'}, '']},
    ]}
    apple = resources['AppleIdentityProvider']
    assert apple['Condition'] == 'AppleSignInEnabled'
    assert apple['Properties']['ProviderType'] == 'SignInWithApple'
    providers = []
    for provider in resources['CognitoUserPoolClient']['Properties']['SupportedIdentityProviders']:
        if isinstance(provider, dict):
            condition, enabled, disabled = provider['Fn::If']
            assert condition == 'AppleSignInEnabled'
            assert enabled == {'Ref': 'AppleIdentityProvider'}
            assert disabled == {'Ref': 'AWS::NoValue'}
            if configured:
                providers.append(apple['Properties']['ProviderName'])
        else:
            providers.append(provider)
    assert providers == ['COGNITO', 'Google'] + (['SignInWithApple'] if configured else [])


def test_apple_credentials_are_dynamic_references_and_never_lambda_environment_values():
    resources = template()['Resources']
    apple = resources['AppleIdentityProvider']['Properties']
    assert apple['ProviderDetails']['authorize_scopes'] == 'email name'
    assert apple['AttributeMapping'] == {'email': 'email', 'username': 'sub', 'name': 'name'}
    for key, field in [('client_id', 'services_id'), ('team_id', 'team_id'), ('key_id', 'key_id'), ('private_key', 'private_key')]:
        assert apple['ProviderDetails'][key] == {
            'Sub': '{{resolve:secretsmanager:${AppleCredentialsSecretArn}:SecretString:' + field + '}}',
        }
    for resource in resources.values():
        if resource['Type'] == 'AWS::Lambda::Function':
            environment = resource['Properties'].get('Environment', {}).get('Variables', {})
            assert all('APPLE' not in key.upper() for key in environment)
            assert 'AppleCredentialsSecretArn' not in str(environment)
