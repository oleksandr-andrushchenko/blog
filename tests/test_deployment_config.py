"""Check deployment command expansion using only synthetic configuration."""
import os
from pathlib import Path
import subprocess


ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))


def test_deployment_reads_credentials_without_echoing_them(tmp_path, monkeypatch):
    values = {
        'AWS_STACK': 'example-stack',
        'THREADS_APP_ID': 'synthetic-threads-app',
        'THREADS_APP_SECRET': 'synthetic-threads-secret',
        'THREADS_TOKEN_ENCRYPTION_KEY': 'synthetic-encryption-key',
        'LINKEDIN_APP_ID': 'synthetic-linkedin-app',
        'LINKEDIN_APP_SECRET': 'synthetic-linkedin-secret',
        'LINKEDIN_TOKEN_ENCRYPTION_KEY': 'synthetic-linkedin-key',
        'LINKEDIN_API_VERSION': '202610',
        'APPLE_CREDENTIALS_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:apple-example',
    }
    (tmp_path / '.env.prod').write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    (tmp_path / '.env').write_text('')
    (tmp_path / '.env.test').write_text('')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    # The Makefile detects Docker at parse time. No Docker or AWS calls should
    # execute during this dry run, including if the deployment recipe changes.
    for name in ('docker', 'aws'):
        binary = binaries / name
        binary.write_text('#!/bin/sh\necho "Unexpected deployment execution" >&2\nexit 99\n')
        binary.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key not in values}
    env['PATH'] = str(binaries) + os.pathsep + env.get('PATH', '')
    result = subprocess.run(['make', '--dry-run', 'PRODUCTION_LOCK_HELD=1', '--file', str(ROOT / 'Makefile'), 'deploy-infra'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'production_config.py" deploy' in result.stdout
    for key in ('THREADS_APP_SECRET', 'LINKEDIN_APP_SECRET', 'THREADS_TOKEN_ENCRYPTION_KEY'):
        assert values[key] not in result.stdout
    from production_config import CF_PARAMETERS

    assert CF_PARAMETERS['ThreadsAppSecret'] == 'THREADS_APP_SECRET'
    assert CF_PARAMETERS['LinkedInAppSecret'] == 'LINKEDIN_APP_SECRET'
    assert CF_PARAMETERS['AppleCredentialsSecretArn'] == 'APPLE_CREDENTIALS_SECRET_ARN'


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


def test_linkedin_credentials_are_api_owned():
    from test_apple_signin import template

    config = template()
    for parameter in ('LinkedInAppSecret', 'LinkedInTokenEncryptionKey'):
        assert config['Parameters'][parameter]['NoEcho'] is True
    resources = config['Resources']
    api = resources['ApiFunction']['Properties']['Environment']['Variables']
    web = resources['WebFunction']['Properties']['Environment']['Variables']
    for variable, parameter in (('LINKEDIN_APP_ID', 'LinkedInAppId'),
                                ('LINKEDIN_APP_SECRET', 'LinkedInAppSecret'),
                                ('LINKEDIN_TOKEN_ENCRYPTION_KEY', 'LinkedInTokenEncryptionKey'),
                                ('LINKEDIN_API_VERSION', 'LinkedInApiVersion')):
        assert api[variable] == {'Ref': parameter}
        assert variable not in web
