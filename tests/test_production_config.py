"""AWS-backed config is resolved by tooling, never by Lambda cold starts."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'scripts'))
import production_config as config


@pytest.fixture
def stores():
    settings = {'AWS_OWNER': 'example-owner', 'APP_STAGE': 'prod', 'APP_ENV': 'prod', 'APP_DEBUG': '0',
                'DOMAIN_NAME': 'example.test', 'HOSTED_ZONE_ID': 'Z123', 'THREADS_APP_ID': 'example-app',
                'GOOGLE_OAUTH_CLIENT_ID': 'example-google-client'}
    credentials = {'APP_SECRET': 'app-private-value', 'AUTH_JWT_SECRET': 'jwt-private-value',
                   'THREADS_APP_SECRET': 'threads-private-value', 'THREADS_TOKEN_ENCRYPTION_KEY': 'key-private-value',
                   'TELEGRAM_PUBLISHER_BOT_TOKEN': '', 'OPENAI_API_KEY': 'script-private-value'}
    bootstrap = {'AWS_PROJECT': 'example-profile', 'AWS_REGION': 'us-west-2', 'AWS_STACK': 'example-stack',
                 'PROD_CONFIG_PREFIX': '/example/prod/', 'PROD_SECRETS_PREFIX': 'example/prod/'}
    calls = []

    def reader(bootstrap, *arguments, payload=None):
        calls.append((arguments, payload))
        if arguments[:2] == ('ssm', 'get-parameters'):
            names = payload['Names']
            parameters = [{'Name': name, 'Type': 'String', 'Value': settings[name.rsplit('/', 1)[1]]}
                          for name in names if settings.get(name.rsplit('/', 1)[1])]
            return {'Parameters': parameters,
                    'InvalidParameters': [name for name in names if not settings.get(name.rsplit('/', 1)[1])]}
        if arguments[:2] == ('ssm', 'get-parameter'):
            return {'Parameter': {'Type': 'String', 'Value': settings[arguments[3].rsplit('/', 1)[1]]}}
        if arguments[:2] == ('secretsmanager', 'get-secret-value'):
            name = arguments[3]
            key = name.rsplit('/', 1)[1]
            if not credentials.get(key):
                raise config.StoreError('secretsmanager', 'get-secret-value', 'ResourceNotFoundException')
            return {'ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:' + name + '-abcdef',
                    'Name': name, 'SecretString': credentials[key]}
        return {}

    return SimpleNamespace(settings=settings, credentials=credentials, bootstrap=bootstrap,
                           calls=calls, reader=reader)


def test_credentials_do_not_reach_make(stores):
    environment = config.deployment_environment(stores.bootstrap, {'APP_SECRET': 'stale-host-secret'}, stores.reader)
    assert environment['PROD_CONFIG_LOADED'] == '1'
    assert environment['APP_SECRET'] == ''
    assert 'CONFIG_REVISION' not in environment
    assert environment['THREADS_APP_ID'] == 'example-app'
    assert environment['TELEGRAM_PUBLISHER_BOT_TOKEN'] == ''
    assert environment['LINKEDIN_APP_SECRET'] == ''
    for secret in stores.credentials.values():
        if secret:
            assert secret not in str(environment)
    assert len(stores.calls) == (len(config.CONFIG_KEYS) + 9) // 10 + len(config.SECRET_KEYS)
    for arguments, payload in stores.calls:
        if arguments[:2] == ('ssm', 'get-parameters'):
            assert len(payload['Names']) <= 10
            assert all(name.startswith('/example/prod/') for name in payload['Names'])


def test_current_values_are_read_by_name_without_version_tracking(stores):
    first_settings, first_credentials = config.cloud_values(stores.bootstrap, stores.reader)
    stores.credentials['APP_SECRET'] = 'replacement-private-value'
    second_settings, second_credentials = config.cloud_values(stores.bootstrap, stores.reader)
    assert first_credentials['APP_SECRET'] == 'app-private-value'
    assert second_credentials['APP_SECRET'] == 'replacement-private-value'
    assert first_settings == second_settings
    assert all('--version-id' not in arguments and '--version-stage' not in arguments
               for arguments, _ in stores.calls)


def test_scripts_receive_raw_values_only_in_process_environment(stores):
    environment = config.script_environment(stores.bootstrap, {
        'PATH': '/usr/bin', 'DYNAMODB_ENDPOINT': 'http://local-db:8000',
        'APP_SECRET': 'stale', 'LINKEDIN_APP_SECRET': 'stale', 'APP_STAGE': 'local',
    }, stores.reader)
    assert environment['APP_SECRET'] == stores.credentials['APP_SECRET']
    assert environment['OPENAI_API_KEY'] == stores.credentials['OPENAI_API_KEY']
    assert environment['LINKEDIN_APP_SECRET'] == ''
    assert environment['APP_STAGE'] == 'prod'
    assert environment['AWS_PROFILE'] == 'example-profile'
    assert 'DYNAMODB_ENDPOINT' not in environment
    assert environment['PATH'] == '/usr/bin'


@pytest.mark.parametrize('value', ['$(shell touch /tmp/unsafe)', '`touch /tmp/unsafe`',
                                   'bad"; echo unsafe', 'value\nOTHER=bad', 'https://example.test/?a=1&b=2'])
def test_deployment_refuses_make_or_shell_injection(stores, value):
    stores.settings['DOMAIN_NAME'] = value
    with pytest.raises(config.ConfigurationError):
        config.deployment_environment(stores.bootstrap, {}, stores.reader)


def test_fail_closed_on_mixed_local_cloud_config_and_secret_in_plain_settings(stores):
    with pytest.raises(config.ConfigurationError, match='bootstrap-only'):
        config.deployment_environment({**stores.bootstrap, 'APP_SECRET': 'obsolete'}, {}, stores.reader)
    stores.settings['APP_SECRET'] = 'must-not-be-used-from-ssm'
    del stores.credentials['APP_SECRET']
    with pytest.raises(config.ConfigurationError, match='APP_SECRET and AUTH_JWT_SECRET'):
        config.deployment_environment(stores.bootstrap, {}, stores.reader)


def legacy_file(tmp_path, stores):
    path = tmp_path / '.env.prod'
    values = {k: v for k, v in stores.bootstrap.items() if k not in ('PROD_CONFIG_PREFIX', 'PROD_SECRETS_PREFIX')}
    values.update(stores.settings, **stores.credentials, WEB_LAMBDA_CODE_TIMESTAMP='saved-key', CSS_CACHE_COUNTER='42')
    path.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
    return path


def test_migration_dry_run_is_redacted_and_performs_no_aws_calls(tmp_path, stores, capsys):
    path = legacy_file(tmp_path, stores)
    original = path.read_bytes()
    config.migrate(path, '/example/prod/', 'example/prod/', reader=stores.reader)
    assert not stores.calls
    assert path.read_bytes() == original
    report = capsys.readouterr().out
    assert 'APP_SECRET' in report and 'app-private-value' not in report
    records = [json.loads(line) for line in report.splitlines()]
    assert len(records) == len(stores.settings) + len(stores.credentials)
    assert all(record['output']['apply'] is False for record in records)
    secret = next(record for record in records if record['input']['key'] == 'APP_SECRET')
    assert secret['output']['name'] == 'example/prod/APP_SECRET'
    assert secret['output']['service'] == 'secretsmanager'


def test_apply_verifies_stores_before_atomically_replacing_symlink_target(tmp_path, stores, capsys):
    original = legacy_file(tmp_path, stores)
    link = tmp_path / 'linked.env'
    link.symlink_to(original)
    config.migrate(link, '/example/prod/', 'example/prod/', apply=True, reader=stores.reader)
    assert link.is_symlink()
    bootstrap = config.read_env(original)
    assert bootstrap['WEB_LAMBDA_CODE_TIMESTAMP'] == 'saved-key'
    assert bootstrap['CSS_CACHE_COUNTER'] == '42'
    assert bootstrap['PROD_CONFIG_PREFIX'] == '/example/prod/'
    assert not set(bootstrap) & (config.CONFIG_KEYS | config.SECRET_KEYS)
    assert original.stat().st_mode & 0o777 == 0o600
    assert 'app-private-value' not in capsys.readouterr().out
    writes = [(args, payload) for args, payload in stores.calls if args[1] in ('create-secret', 'put-parameter')]
    assert len(writes) == len(stores.settings) + sum(bool(v) for v in stores.credentials.values())
    for args, payload in writes:
        key = payload['Name'].rsplit('/', 1)[1]
        if args[0] == 'secretsmanager':
            assert payload['SecretString'] == stores.credentials[key]
        else:
            assert payload['Value'] == stores.settings[key]
            assert payload['Overwrite'] is False


def test_apply_failure_keeps_original_local_values(tmp_path, stores):
    path = legacy_file(tmp_path, stores)
    original = path.read_bytes()

    def failed(bootstrap, *arguments, payload=None):
        raise config.ConfigurationError('AWS write failed')

    with pytest.raises(config.ConfigurationError):
        config.migrate(path, '/example/prod/', 'example/prod/', apply=True, reader=failed)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('.prod-bootstrap-*'))


def test_unknown_credentials_require_classification_before_migration(tmp_path, stores):
    path = legacy_file(tmp_path, stores)
    with path.open('a') as output:
        output.write('NEW_PROVIDER_API_KEY=private\n')
    with pytest.raises(config.ConfigurationError, match='Classify these keys'):
        config.migrate(path, '/example/prod/', 'example/prod/', reader=stores.reader)
    assert not stores.calls


def test_cli_write_uses_memory_only_payload_and_never_echoes_values_on_failure(monkeypatch, stores):
    import shutil
    calls = []
    monkeypatch.setattr(shutil, 'which', lambda _: '/bin/aws')

    def run(command, **kwargs):
        payload = json.loads(os.pread(kwargs['pass_fds'][0], 1048576, 0))
        calls.append((command, {**kwargs, 'decoded_payload': payload}))
        return SimpleNamespace(returncode=1, stdout='', stderr='provider echoed app-private-value')

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(config.ConfigurationError) as error:
        config.aws_json(stores.bootstrap, 'secretsmanager', 'create-secret', payload={'SecretString': 'app-private-value'})
    assert 'app-private-value' not in str(error.value)
    assert 'app-private-value' not in str(calls[0][0])
    assert calls[0][1]['decoded_payload']['SecretString'] == 'app-private-value'


def test_make_cloud_dry_run_contains_no_credential_values(tmp_path, stores):
    bootstrap = tmp_path / '.env.prod'
    bootstrap.write_text('AWS_STACK=example-stack\nAWS_PROJECT=example-profile\nAWS_REGION=us-west-2\n')
    (tmp_path / '.env.test').write_text('')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for name in ('docker', 'aws'):
        path = binaries / name
        path.write_text('#!/bin/sh\nexit 99\n')
        path.chmod(0o755)
    environment = config.deployment_environment(stores.bootstrap, os.environ, stores.reader)
    environment['PATH'] = str(binaries) + os.pathsep + environment.get('PATH', '')
    result = subprocess.run(['make', '--dry-run', '--file', str(ROOT / 'Makefile'),
                             'PRODUCTION_LOCK_HELD=1', 'deploy-infra'], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'production_config.py" deploy' in result.stdout
    assert '{{resolve:secretsmanager:' not in result.stdout
    for secret in stores.credentials.values():
        if secret:
            assert secret not in result.stdout + result.stderr


def test_no_refresh_marker_or_runtime_resolver_is_added(stores):
    from test_apple_signin import template
    infrastructure = template()
    for resource in infrastructure['Resources'].values():
        if resource['Type'] == 'AWS::Lambda::Function':
            assert 'CONFIG_REVISION' not in resource['Properties']['Environment']['Variables']
    pattern = infrastructure['Parameters']['TelegramPublisherWebhookSecret']['AllowedPattern']
    assert re.fullmatch(pattern, 'a' * 32)
    assert 'ConfigRevision' not in infrastructure['Parameters']
    composition = yaml.safe_load((ROOT / 'docker-compose.scripts.production.yml').read_text())
    service = composition['services']['scripts-production']
    assert 'env_file' not in service
    assert service['entrypoint'][1] == '/app/scripts/production_config.py'
    assert not set(service['environment']) & config.SECRET_KEYS
    assert not any(isinstance(volume, dict) and volume.get('target') == '/app/.env.prod'
                   for volume in service['volumes'])
    assert any(isinstance(volume, dict) and volume.get('target') == '/production'
               and volume.get('read_only') is False for volume in service['volumes'])
    assert not (ROOT / 'shared/production_config.py').exists()


def test_interrupted_migration_reuses_only_identical_stores(tmp_path, stores):
    path = legacy_file(tmp_path, stores)

    def already_exists(bootstrap, *arguments, payload=None):
        if arguments[:2] == ('secretsmanager', 'create-secret'):
            raise config.StoreError('secretsmanager', 'create-secret', 'ResourceExistsException')
        if arguments[:2] == ('ssm', 'put-parameter'):
            raise config.StoreError('ssm', 'put-parameter', 'ParameterAlreadyExists')
        return stores.reader(bootstrap, *arguments, payload=payload)

    config.migrate(path, '/example/prod/', 'example/prod/', apply=True, reader=already_exists)
    assert config.read_env(path)['PROD_CONFIG_PREFIX'] == '/example/prod/'


def test_existing_different_store_preserves_local_source(tmp_path, stores):
    path = legacy_file(tmp_path, stores)
    original = path.read_bytes()

    def different(bootstrap, *arguments, payload=None):
        if arguments[:2] == ('secretsmanager', 'create-secret'):
            raise config.StoreError('secretsmanager', 'create-secret', 'ResourceExistsException')
        return {'SecretString': 'another-application'}

    with pytest.raises(config.ConfigurationError, match='Existing record differs'):
        config.migrate(path, '/example/prod/', 'example/prod/', apply=True, reader=different)
    assert path.read_bytes() == original


def test_sdk_fallback_uses_profile_and_never_leaks_provider_errors(monkeypatch, stores):
    import boto3
    from botocore.exceptions import ClientError
    import shutil
    calls = []
    monkeypatch.setattr(shutil, 'which', lambda _: None)

    def get_parameter(**kwargs):
        assert kwargs == {'Name': '/example/prod/'}
        raise ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'raw-sensitive-body'}}, 'GetParameter')

    def session(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(client=lambda name: SimpleNamespace(get_parameter=get_parameter))

    monkeypatch.setattr(boto3, 'Session', session)
    with pytest.raises(config.StoreError) as error:
        config.aws_json(stores.bootstrap, 'ssm', 'get-parameter', '--name', '/example/prod/')
    assert calls == [{'profile_name': 'example-profile', 'region_name': 'us-west-2'}]
    assert 'raw-sensitive-body' not in str(error.value)
    assert error.value.code == 'AccessDeniedException'


def test_lambda_bundle_does_not_include_production_bootstrap():
    # The application reads pre-injected env; adding this helper to a shared module
    # would silently introduce runtime/network dependencies into both ZIPs.
    builder = (ROOT / 'scripts/generate_lambda_build.py').read_text()
    assert 'production_config' not in builder
    for directory in ('shared', 'web-lambda', 'api-lambda'):
        for path in (ROOT / directory).glob('*.py'):
            assert 'import production_config' not in path.read_text()


def test_lock_resolves_cloud_snapshot_before_starting_child(tmp_path, stores, monkeypatch):
    from production_lock import run_locked
    bootstrap = tmp_path / '.env.prod'
    bootstrap.write_text(''.join(f'{k}={v}\n' for k, v in stores.bootstrap.items()))
    calls = []

    def snapshot(values, inherited):
        # The child cannot start before cloud loading completes.
        assert values == stores.bootstrap
        calls.append('loaded')
        assert inherited['PRODUCTION_LOCK_HELD'] == '1'
        return {**inherited, 'PROD_CONFIG_LOADED': '1', 'DOMAIN_NAME': 'snapshot.example.test'}

    monkeypatch.setattr(config, 'deployment_environment', snapshot)
    result = run_locked(bootstrap, [sys.executable, '-c',
        'import os; assert os.environ["PROD_CONFIG_LOADED"] == "1"; '
        'assert os.environ["DOMAIN_NAME"] == "snapshot.example.test"'])
    assert result == 0
    assert calls == ['loaded']


def test_bad_cloud_bootstrap_never_starts_deployment(tmp_path, monkeypatch):
    from production_lock import run_locked
    bootstrap = tmp_path / '.env.prod'
    bootstrap.write_text('AWS_STACK=example\nAWS_PROJECT=example\nAWS_REGION=us-west-2\n'
                         'PROD_CONFIG_PREFIX=/example/settings\n')
    child = tmp_path / 'child-started'
    with pytest.raises(config.ConfigurationError):
        run_locked(bootstrap, [sys.executable, '-c', f'open({str(child)!r}, "w").close()'])
    assert not child.exists()


@pytest.mark.parametrize('cloud', [False, True])
def test_deploy_sends_current_values_only_through_memory_descriptor(tmp_path, stores, monkeypatch, cloud):
    values = stores.bootstrap if cloud else {
        **{key: value for key, value in stores.bootstrap.items() if key not in ('PROD_CONFIG_PREFIX', 'PROD_SECRETS_PREFIX')},
        **stores.settings, **stores.credentials,
    }
    values = {**values, **{prefix + '_LAMBDA_CODE_TIMESTAMP': '20261105010203' for prefix in ('WEB', 'API', 'IMG')}}
    path = tmp_path / '.env.prod'
    path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    calls = []

    def run(command, **kwargs):
        payload = json.loads(os.pread(kwargs['pass_fds'][0], 1048576, 0))
        calls.append((command, {**kwargs, 'decoded_payload': payload}))
        return SimpleNamespace(returncode=0, stdout='provider output', stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    certificate = 'arn:aws:acm:us-east-1:123456789012:certificate/example'
    assert config.deploy(path, certificate, certificate, reader=stores.reader) == 0
    command, kwargs = calls[0]
    payload = {item['ParameterKey']: item['ParameterValue'] for item in kwargs['decoded_payload']}
    assert payload['Secret'] == stores.credentials['APP_SECRET']
    assert payload['AuthJwtSecret'] == stores.credentials['AUTH_JWT_SECRET']
    assert payload['GoogleOauthClientId'] == stores.settings['GOOGLE_OAUTH_CLIENT_ID']
    assert payload['WebFuncS3Key'] == 'web-function-20261105010203.zip'
    assert payload['ApiFuncS3Key'] == 'api-function-20261105010203.zip'
    assert payload['ImgFuncS3Key'] == 'img-function-20261105010203.zip'
    assert payload['CertificateArn'] == certificate
    assert command[command.index('--parameter-overrides') + 1].startswith('file:///proc/self/fd/')
    assert 'input' not in kwargs
    assert command[command.index('--s3-bucket') + 1] == 'example-stack-code'
    assert command[command.index('--s3-prefix') + 1] == 'cloudformation'
    assert kwargs['capture_output'] is True
    assert all(value not in str(command) for value in stores.credentials.values() if value)
    assert 'ConfigRevision' not in payload
    assert not (set(payload) & {'OPENAI_API_KEY', 'PEXELS_API_KEY'})
    from test_apple_signin import template
    assert set(payload) == set(template()['Parameters'])


def test_deployment_error_does_not_echo_provider_secrets(tmp_path, stores, monkeypatch, capsys):
    path = tmp_path / '.env.prod'
    values = {**stores.bootstrap, **{prefix + '_LAMBDA_CODE_TIMESTAMP': '20261105010203'
                                   for prefix in ('WEB', 'API', 'IMG')}}
    path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout='app-private-value', stderr='jwt-private-value'))
    certificate = 'arn:aws:acm:us-east-1:123456789012:certificate/example'
    with pytest.raises(config.ConfigurationError) as error:
        config.deploy(path, certificate, certificate, reader=stores.reader)
    assert 'app-private-value' not in str(error.value) + capsys.readouterr().out
    assert 'jwt-private-value' not in str(error.value)


def test_actual_aws_cli_can_parse_memory_only_parameter_payload():
    import shutil
    executable = shutil.which('aws')
    if not executable:
        pytest.skip('AWS CLI is not installed in this test environment')
    # Output skeleton validation parses inputs locally and never sends a provider request.
    result = config.run_aws_cli([executable, 'ssm', 'put-parameter', '--region', 'us-west-2',
                                 '--generate-cli-skeleton', 'output'],
                                {'Name': '/example/test/APP_DEBUG', 'Type': 'String',
                                 'Value': '0', 'Overwrite': False})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('apply', [False, True])
def test_relocate_preserves_values_and_switches_only_after_readback(tmp_path, stores, capsys, apply):
    path = tmp_path / '.env.prod'
    original = {**stores.bootstrap, 'PROD_CONFIG_PREFIX': '/example/prod/settings/',
                'PROD_SECRETS_PREFIX': 'example/prod/secrets/', 'CSS_CACHE_COUNTER': '42'}
    path.write_text(''.join(f'{k}={v}\n' for k, v in original.items()))
    link = tmp_path / 'linked.env'
    link.symlink_to(path)
    config.relocate(link, '/example/prod/', 'example/prod/', apply=apply, reader=stores.reader)
    assert link.is_symlink()
    expected = {**original, 'PROD_CONFIG_PREFIX': '/example/prod/',
                'PROD_SECRETS_PREFIX': 'example/prod/'} if apply else original
    assert config.read_env(path) == expected
    writes = [(args, payload) for args, payload in stores.calls if args[1] in ('create-secret', 'put-parameter')]
    assert len(writes) == (len(stores.settings) + sum(bool(v) for v in stores.credentials.values()) if apply else 0)
    for args, payload in writes:
        key = payload['Name'].rsplit('/', 1)[1]
        assert payload.get('Value', payload.get('SecretString')) == {**stores.settings, **stores.credentials}[key]
        assert '/settings/' not in payload['Name'] and '/secrets/' not in payload['Name']
    assert not any(args[1].startswith('delete') for args, _ in stores.calls)
    report = capsys.readouterr().out
    assert all(value not in report for value in stores.credentials.values() if value)
    if apply:
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('failure', ['write', 'readback', 'concurrent'])
def test_relocate_failure_preserves_active_bootstrap(tmp_path, stores, failure):
    path = tmp_path / '.env.prod'
    path.write_text(''.join(f'{k}={v}\n' for k, v in stores.bootstrap.items()))
    original = path.read_bytes()

    def reader(bootstrap, *arguments, payload=None):
        if arguments[1] in ('create-secret', 'put-parameter'):
            if failure == 'write':
                raise config.StoreError(arguments[0], arguments[1])
            if failure == 'concurrent':
                path.write_bytes(original + b'CSS_CACHE_COUNTER=43\n')
        result = stores.reader(bootstrap, *arguments, payload=payload)
        if failure == 'readback' and bootstrap['PROD_CONFIG_PREFIX'] == '/example/new/':
            if arguments[:2] == ('secretsmanager', 'get-secret-value') and arguments[3].endswith('/APP_SECRET'):
                result['SecretString'] = 'unexpected-value'
        return result

    with pytest.raises(config.ConfigurationError):
        config.relocate(path, '/example/new/', 'example/new/', apply=True, reader=reader)
    assert path.read_bytes() == (original + b'CSS_CACHE_COUNTER=43\n' if failure == 'concurrent' else original)
