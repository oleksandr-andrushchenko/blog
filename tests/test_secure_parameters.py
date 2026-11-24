"""Encrypted-store cutover preserves credentials and never writes plaintext backups."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'scripts'))
import production_config as config
import secure_parameters as secure


@pytest.fixture
def migration(tmp_path):
    if not {'APP_SECRET', 'AUTH_JWT_SECRET', 'GOOGLE_OAUTH_CLIENT_SECRET'} <= config.SECRET_KEYS:
        pytest.skip('This project has no scalar application credentials to migrate')
    settings = {'AWS_OWNER': 'example-owner', 'APP_STAGE': 'prod', 'APP_ENV': 'prod', 'APP_DEBUG': '0',
                'DOMAIN_NAME': 'example.test', 'HOSTED_ZONE_ID': 'Z123',
                'GOOGLE_OAUTH_CLIENT_ID': 'example-client'}
    credentials = {'APP_SECRET': 'synthetic-app-secret', 'AUTH_JWT_SECRET': 'synthetic-jwt-secret',
                   'GOOGLE_OAUTH_CLIENT_SECRET': 'synthetic-oauth-secret'}
    parameters = {'/example/prod/' + key: {'Name': '/example/prod/' + key, 'Type': 'String', 'Value': value}
                  for key, value in settings.items()}
    calls = []
    metadata = {}
    tags = {}

    def reader(bootstrap, service, operation, *arguments, payload=None):
        calls.append((service, operation, arguments, payload))
        if service == 'ssm' and operation == 'get-parameters':
            names = payload['Names']
            return {'Parameters': [parameters[name].copy() for name in names if name in parameters],
                    'InvalidParameters': [name for name in names if name not in parameters]}
        if service == 'ssm' and operation == 'get-parameter':
            if payload['Name'] not in parameters:
                raise config.StoreError(service, operation, 'ParameterNotFound')
            return {'Parameter': parameters[payload['Name']].copy()}
        if service == 'ssm' and operation == 'describe-parameters':
            name = payload['ParameterFilters'][0]['Values'][0]
            return {'Parameters': [{**parameters[name], 'Tier': parameters[name].get('Tier', 'Standard')}]}
        if service == 'ssm' and operation == 'put-parameter':
            assert payload['Overwrite'] is False
            assert payload['Type'] == 'SecureString' and payload['Tier'] == 'Standard'
            parameters[payload['Name']] = {key: payload[key] for key in ('Name', 'Type', 'Value')}
            return {'Version': 1}
        if service == 'ssm' and operation == 'add-tags-to-resource':
            tags[payload['ResourceId']] = payload['Tags']
            return {}
        if service == 'ssm' and operation == 'list-tags-for-resource':
            return {'TagList': tags.get(payload['ResourceId'], [])}
        if service == 'cloudformation':
            return {'Stacks': [{'Tags': [{'Key': 'Project', 'Value': 'example'},
                                        {'Key': 'Owner', 'Value': 'example-owner'}]}]}
        if service == 'kms':
            return {'KeyMetadata': {'KeyManager': 'CUSTOMER', 'Arn': payload['KeyId']}}
        if service == 'secretsmanager':
            name = payload['SecretId'] if payload else arguments[1]
            key = name.rsplit('/', 1)[1]
            if key not in credentials:
                raise config.StoreError(service, operation, 'ResourceNotFoundException')
            if operation == 'describe-secret':
                return {'Name': name, **metadata.get(key, {})}
            return {'Name': name, 'SecretString': credentials[key]}
        raise AssertionError((service, operation))

    bootstrap = {'AWS_STACK': 'example', 'AWS_PROJECT': 'example', 'AWS_REGION': 'us-west-2',
                 'PROD_CONFIG_PREFIX': '/example/prod/', 'PROD_SECRETS_PREFIX': 'example/prod/',
                 'WEB_LAMBDA_CODE_TIMESTAMP': '20260101000000', 'CSS_CACHE_COUNTER': '42'}
    path = tmp_path / '.env.prod'
    path.write_text(''.join(f'{key}={value}\n' for key, value in bootstrap.items()))
    return SimpleNamespace(path=path, reader=reader, bootstrap=bootstrap, credentials=credentials,
                           settings=settings, parameters=parameters, metadata=metadata, calls=calls, tags=tags)


def test_preview_has_no_writes_no_values_and_preserves_bootstrap(migration, capsys):
    original = migration.path.read_bytes()
    secure.migrate(config, migration.path, reader=migration.reader)
    assert migration.path.read_bytes() == original
    assert not any(operation in {'put-parameter', 'add-tags-to-resource'}
                   for _, operation, _, _ in migration.calls)
    output = capsys.readouterr().out
    assert all(value not in output for value in migration.credentials.values())
    assert 'SecureString' in output


def test_apply_verifies_all_values_then_switches_bootstrap_and_preserves_sources(migration, capsys):
    link = migration.path.parent / 'linked.env'
    link.symlink_to(migration.path)
    original_credentials = migration.credentials.copy()
    secure.migrate(config, link, apply=True, reader=migration.reader)
    assert link.is_symlink()
    bootstrap = config.read_env(migration.path)
    assert bootstrap == {**migration.bootstrap, 'PROD_SECRET_STORE': 'ssm'}
    assert migration.path.stat().st_mode & 0o777 == 0o600
    assert migration.credentials == original_credentials
    assert config.cloud_values(bootstrap, migration.reader) == (migration.settings, migration.credentials)
    for key, value in migration.credentials.items():
        parameter = migration.parameters['/example/prod/' + key]
        assert parameter['Type'] == 'SecureString' and parameter['Value'] == value
    assert all({'Key': 'Owner', 'Value': 'example-owner'} in tags for tags in migration.tags.values())
    assert set(migration.tags) == set(migration.parameters)
    assert not list(migration.path.parent.glob('.prod-bootstrap-*'))
    assert all(value not in capsys.readouterr().out for value in migration.credentials.values())
    migration.calls.clear()
    secure.migrate(config, link, apply=True, reader=migration.reader)
    assert not any(operation == 'put-parameter' for _, operation, _, _ in migration.calls)


@pytest.mark.parametrize('parameter_type,value', [('String', 'synthetic-app-secret'), ('SecureString', 'different')])
def test_conflicting_or_plaintext_destination_prevents_all_writes(migration, parameter_type, value):
    name = '/example/prod/APP_SECRET'
    migration.parameters[name] = {'Name': name, 'Type': parameter_type, 'Value': value}
    original = migration.path.read_bytes()
    with pytest.raises(config.ConfigurationError, match='Existing destination differs'):
        secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    assert migration.path.read_bytes() == original
    assert not any(operation in {'put-parameter', 'add-tags-to-resource'}
                   for _, operation, _, _ in migration.calls)


@pytest.mark.parametrize('properties', [{'RotationEnabled': True}, {'ReplicationStatus': [{}]}, {'DeletedDate': 1},
                                        {'OwningService': 'provider'},
                                        {'Tags': [{'Key': 'aws:cloudformation:stack-id', 'Value': 'stack'}]}])
def test_automated_secret_lifecycles_are_not_migrated(migration, properties):
    migration.metadata['APP_SECRET'] = properties
    with pytest.raises(config.ConfigurationError, match='separate review'):
        secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    assert not any(operation == 'put-parameter' for _, operation, _, _ in migration.calls)


def test_customer_managed_encryption_key_is_preserved(migration):
    arn = 'arn:aws:kms:us-west-2:123456789012:key/example-key'
    migration.metadata['APP_SECRET'] = {'KmsKeyId': arn}
    secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    payload = next(payload for _, operation, _, payload in migration.calls
                   if operation == 'put-parameter' and payload['Name'].endswith('/APP_SECRET'))
    assert payload['KeyId'] == arn


def test_source_changes_prevent_bootstrap_cutover(migration):
    original = migration.path.read_bytes()

    def changed(*args, **kwargs):
        result = migration.reader(*args, **kwargs)
        if args[1:3] == ('ssm', 'put-parameter'):
            migration.credentials[kwargs['payload']['Name'].rsplit('/', 1)[1]] = 'changed-source'
        return result

    with pytest.raises(config.ConfigurationError, match='Source changed'):
        secure.migrate(config, migration.path, apply=True, reader=changed)
    assert migration.path.read_bytes() == original


def test_tag_readback_failure_prevents_bootstrap_cutover(migration):
    original = migration.path.read_bytes()

    def missing_tags(*args, **kwargs):
        if args[1:3] == ('ssm', 'list-tags-for-resource'):
            return {'TagList': []}
        return migration.reader(*args, **kwargs)

    with pytest.raises(config.ConfigurationError, match='tag readback differs'):
        secure.migrate(config, migration.path, apply=True, reader=missing_tags)
    assert migration.path.read_bytes() == original


def test_readback_failure_preserves_old_store_selection(migration):
    original = migration.path.read_bytes()

    def corrupt(*args, **kwargs):
        result = migration.reader(*args, **kwargs)
        if args[1:3] == ('ssm', 'put-parameter'):
            migration.parameters[kwargs['payload']['Name']]['Value'] = 'corrupted'
        return result

    with pytest.raises(config.ConfigurationError, match='readback differs'):
        secure.migrate(config, migration.path, apply=True, reader=corrupt)
    assert migration.path.read_bytes() == original


def test_secure_loader_never_falls_back_to_a_stale_secret(migration):
    secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    del migration.parameters['/example/prod/APP_SECRET']
    migration.calls.clear()
    with pytest.raises(config.ConfigurationError):
        config.cloud_values(config.read_env(migration.path), migration.reader)
    assert not any(service == 'secretsmanager' and arguments[-1].endswith('/APP_SECRET')
                   for service, _, arguments, _ in migration.calls if arguments)


def test_invalid_secure_parameter_envelope_is_rejected(migration):
    def incomplete(*args, **kwargs):
        return {'Parameters': [], 'InvalidParameters': []}

    with pytest.raises(config.ConfigurationError, match='Incomplete encrypted'):
        secure.read_credentials(migration.bootstrap, {'APP_SECRET'}, incomplete, config.ConfigurationError)


def test_unknown_store_is_rejected(migration):
    with pytest.raises(config.ConfigurationError, match='Unknown production credential store'):
        config.cloud_values({**migration.bootstrap, 'PROD_SECRET_STORE': 'unknown'}, migration.reader)


def test_empty_secret_inventory_requires_no_aws_requests():
    def unexpected(*args, **kwargs):
        raise AssertionError('No AWS request is needed')

    assert secure.read_credentials({'PROD_CONFIG_PREFIX': '/example/prod/'}, set(), unexpected,
                                   config.ConfigurationError) == {}


def test_worktree_bootstrap_copies_encrypted_reader_dependency(tmp_path):
    from worktree_production import configure_worktree_production

    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / '.env.prod').write_text('AWS_PROJECT=example\nPROD_SECRET_STORE=ssm\n')
    configure_worktree_production(source, target)
    assert (target / '.env.prod').is_symlink()
    for filename in ('production_config.py', 'production_lock.py', 'secure_parameters.py'):
        assert (target / 'scripts' / filename).read_bytes() == (ROOT / 'scripts' / filename).read_bytes()


@pytest.mark.parametrize('tier', ['Advanced', 'Intelligent-Tiering'])
def test_existing_nonstandard_parameter_prevents_all_writes(migration, tier):
    name = '/example/prod/APP_SECRET'
    migration.parameters[name] = {'Name': name, 'Type': 'SecureString',
                                  'Value': migration.credentials['APP_SECRET'], 'Tier': tier}
    with pytest.raises(config.ConfigurationError, match='Standard tier'):
        secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    assert not any(operation in {'put-parameter', 'add-tags-to-resource'}
                   for _, operation, _, _ in migration.calls)


def test_provider_documents_are_outside_scalar_inventory(migration):
    provider_keys = {'APPLE_CREDENTIALS_SECRET_ARN', 'FACEBOOK_CREDENTIALS_SECRET_ARN',
                     'GOOGLE_SEARCH_CONSOLE_CREDENTIALS_SECRET_ARN', 'COGNITO_CLIENT_SECRET'}
    assert not provider_keys & config.SECRET_KEYS
    migration.settings['APPLE_CREDENTIALS_SECRET_ARN'] = 'arn:aws:secretsmanager:example:apple'
    name = '/example/prod/APPLE_CREDENTIALS_SECRET_ARN'
    migration.parameters[name] = {'Name': name, 'Type': 'String',
                                  'Value': migration.settings['APPLE_CREDENTIALS_SECRET_ARN']}
    secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    assert not any(service == 'secretsmanager' and (
        (payload and payload.get('SecretId') in migration.settings.values())
        or (arguments and arguments[-1] in migration.settings.values()))
        for service, _, arguments, payload in migration.calls)


def test_legacy_relocation_cannot_move_secure_bootstrap(migration):
    config.write_bootstrap(migration.path, {**migration.bootstrap, 'PROD_SECRET_STORE': 'ssm'})
    with pytest.raises(config.ConfigurationError, match='dedicated relocation'):
        config.relocate(migration.path, '/example/new/', 'example/new/', apply=True, reader=migration.reader)
    assert not migration.calls


@pytest.mark.parametrize('value', ['x' * 4097, 'é' * 2049])
def test_oversized_credentials_fail_before_any_write(migration, value):
    migration.credentials['AUTH_JWT_SECRET'] = value
    original = migration.path.read_bytes()
    with pytest.raises(config.ConfigurationError, match='standard encrypted parameter'):
        secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    assert migration.path.read_bytes() == original
    assert not any(operation in {'put-parameter', 'add-tags-to-resource'}
                   for _, operation, _, _ in migration.calls)


def test_concurrent_bootstrap_edit_is_preserved(migration):
    original = migration.path.read_bytes()

    def edited(*args, **kwargs):
        result = migration.reader(*args, **kwargs)
        if args[1:3] == ('ssm', 'put-parameter'):
            migration.path.write_bytes(original + b'JS_CACHE_COUNTER=99\n')
        return result

    with pytest.raises(config.ConfigurationError, match='Bootstrap changed'):
        secure.migrate(config, migration.path, apply=True, reader=edited)
    assert migration.path.read_bytes() == original + b'JS_CACHE_COUNTER=99\n'


def test_provider_tags_and_stack_ownership_tags_are_retained(migration):
    migration.metadata['APP_SECRET'] = {'Tags': [{'Key': 'Purpose', 'Value': 'signing'},
                                               {'Key': 'Owner', 'Value': 'old-owner'}]}
    secure.migrate(config, migration.path, apply=True, reader=migration.reader)
    tags = migration.tags['/example/prod/APP_SECRET']
    assert {'Key': 'Purpose', 'Value': 'signing'} in tags
    assert {'Key': 'Owner', 'Value': 'example-owner'} in tags
