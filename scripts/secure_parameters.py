"""Read and migrate individual application credentials without exposing values."""
import argparse
import json
import os
from pathlib import Path
import tempfile


# This value is generated and owned by CloudFormation, not by configuration tooling.
MANAGED_SECRET_KEYS = frozenset({'COGNITO_CLIENT_SECRET'})


def read_credentials(bootstrap, keys, reader, configuration_error):
    """Read the explicit encrypted namespace without a plaintext/legacy fallback."""
    names = {bootstrap['PROD_CONFIG_PREFIX'] + key: key
             for key in sorted(set(keys) - MANAGED_SECRET_KEYS)}
    result = {}
    ordered = list(names)
    for offset in range(0, len(ordered), 10):
        requested = ordered[offset:offset + 10]
        response = reader(bootstrap, 'ssm', 'get-parameters', payload={
            'Names': requested, 'WithDecryption': True,
        })
        seen = set()
        for parameter in response.get('Parameters', []):
            name, value = parameter.get('Name'), parameter.get('Value')
            if (name not in requested or name in seen or parameter.get('Type') != 'SecureString'
                    or not isinstance(value, str) or not value or '\x00' in value):
                raise configuration_error('Invalid encrypted production parameter response')
            seen.add(name)
            result[names[name]] = value
        missing = response.get('InvalidParameters', [])
        if len(missing) != len(set(missing)) or set(missing) != set(requested) - seen:
            raise configuration_error('Incomplete encrypted production parameter response')
    return result


def migrate(config, path, *, apply=False, reader=None):
    """Copy, verify and tag credentials, retaining legacy records for rollback."""
    reader = reader or config.aws_json
    bootstrap = config.bootstrap_values(path)
    original = Path(path).resolve().read_bytes()
    if not bootstrap.get('PROD_CONFIG_PREFIX') or set(bootstrap) - config.LOCAL_KEYS:
        raise config.ConfigurationError('Migration requires a bootstrap-only cloud configuration')
    config.validate_prefixes(bootstrap['PROD_CONFIG_PREFIX'], bootstrap['PROD_SECRETS_PREFIX'])
    original_settings, original_credentials = config.cloud_values(bootstrap, reader)
    stack = reader(bootstrap, 'cloudformation', 'describe-stacks', payload={
        'StackName': bootstrap['AWS_STACK'],
    })['Stacks'][0]
    tags = {tag['Key']: tag['Value'] for tag in stack.get('Tags', [])
            if not tag['Key'].startswith('aws:')}
    tags.setdefault('Project', bootstrap['AWS_PROJECT'])
    if original_settings.get('AWS_OWNER'):
        tags.setdefault('Owner', original_settings['AWS_OWNER'])
    tags.setdefault('Stage', original_settings['APP_STAGE'])
    tags.setdefault('Region', bootstrap['AWS_REGION'])
    plans = []
    # Preflight every value before the first write. Values stay only in memory.
    for key in sorted(config.SECRET_KEYS - MANAGED_SECRET_KEYS):
        source = bootstrap['PROD_SECRETS_PREFIX'] + key
        destination = bootstrap['PROD_CONFIG_PREFIX'] + key
        try:
            metadata = reader(bootstrap, 'secretsmanager', 'describe-secret', payload={'SecretId': source})
            record = reader(bootstrap, 'secretsmanager', 'get-secret-value', '--secret-id', source)
        except config.StoreError as error:
            if error.code == 'ResourceNotFoundException':
                continue
            raise
        if (metadata.get('RotationEnabled') or metadata.get('ReplicationStatus')
                or metadata.get('DeletedDate') or metadata.get('OwningService')
                or any(tag['Key'].startswith('aws:cloudformation:') for tag in metadata.get('Tags', []))):
            raise config.ConfigurationError('Rotating, replicated, retiring or managed secrets need separate review: ' + key)
        value = record.get('SecretString')
        if (record.get('Name') != source or not isinstance(value, str) or not value
                or '\x00' in value or len(value.encode()) > 4096):
            raise config.ConfigurationError('Credential does not fit a standard encrypted parameter: ' + key)
        existing = _parameter(config, bootstrap, destination, reader)
        if existing and (existing.get('Type') != 'SecureString' or existing.get('Value') != value):
            raise config.ConfigurationError('Existing destination differs: ' + key)
        if existing:
            _verify_standard(config, bootstrap, destination, reader)
        source_tags = {tag['Key']: tag['Value'] for tag in metadata.get('Tags', [])
                       if not tag['Key'].startswith('aws:')}
        source_tags.update(tags)
        key_id = metadata.get('KmsKeyId')
        if key_id:
            encryption = reader(bootstrap, 'kms', 'describe-key', payload={'KeyId': key_id})['KeyMetadata']
            key_id = encryption['Arn'] if encryption.get('KeyManager') == 'CUSTOMER' else 'alias/aws/ssm'
        plans.append({'key': key, 'source': source, 'name': destination, 'value': value,
                      'exists': existing is not None, 'tags': source_tags,
                      'key_id': key_id or 'alias/aws/ssm'})
    for plan in plans:
        print(json.dumps({'key': plan['key'], 'source': plan['source'], 'destination': plan['name'],
                          'type': 'SecureString', 'tier': 'Standard', 'tags': plan['tags'],
                          'action': 'verify existing' if plan['exists'] else 'copy', 'apply': apply}), flush=True)
        if not apply:
            continue
        if not plan['exists']:
            try:
                reader(bootstrap, 'ssm', 'put-parameter', payload={
                    'Name': plan['name'], 'Type': 'SecureString', 'Tier': 'Standard',
                    'KeyId': plan['key_id'], 'Value': plan['value'], 'Overwrite': False,
                    'Tags': _tags(plan['tags']),
                })
            except config.StoreError as error:
                if error.code != 'ParameterAlreadyExists':
                    raise
        actual = _parameter(config, bootstrap, plan['name'], reader)
        if not actual or actual.get('Type') != 'SecureString' or actual.get('Value') != plan['value']:
            raise config.ConfigurationError('Encrypted parameter readback differs: ' + plan['key'])
        _verify_standard(config, bootstrap, plan['name'], reader)
        reader(bootstrap, 'ssm', 'add-tags-to-resource', payload={
            'ResourceType': 'Parameter', 'ResourceId': plan['name'], 'Tags': _tags(plan['tags']),
        })
        _verify_tags(config, bootstrap, plan['name'], plan['tags'], reader)
        # Source changes during a migration must not silently leave a stale target.
        current = reader(bootstrap, 'secretsmanager', 'get-secret-value', '--secret-id', plan['source'])
        if current.get('SecretString') != plan['value']:
            raise config.ConfigurationError('Source changed during migration: ' + plan['key'])
    if apply:
        loaded = read_credentials(bootstrap, config.SECRET_KEYS, reader, config.ConfigurationError)
        if any(loaded.get(plan['key']) != plan['value'] for plan in plans):
            raise config.ConfigurationError('Configuration loader verification failed')
        # Existing ordinary settings receive the same stack ownership tags.
        for key in sorted(config.CONFIG_KEYS):
            name = bootstrap['PROD_CONFIG_PREFIX'] + key
            parameter = _parameter(config, bootstrap, name, reader, decrypt=False)
            if parameter:
                if parameter.get('Type') != 'String':
                    raise config.ConfigurationError('Ordinary setting must remain String: ' + key)
                reader(bootstrap, 'ssm', 'add-tags-to-resource', payload={
                    'ResourceType': 'Parameter', 'ResourceId': name, 'Tags': _tags(tags),
                })
                _verify_tags(config, bootstrap, name, tags, reader)
        destination = {**bootstrap, 'PROD_SECRET_STORE': 'ssm'}
        if config.cloud_values(destination, reader) != (original_settings, original_credentials):
            raise config.ConfigurationError('Full configuration differs; bootstrap has been preserved')
        if config.cloud_values(bootstrap, reader) != (original_settings, original_credentials):
            raise config.ConfigurationError('Source configuration changed; bootstrap has been preserved')
        target = Path(path).resolve()
        descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix='.prod-bootstrap-')
        try:
            with os.fdopen(descriptor, 'w') as output:
                output.write(''.join(f'{key}={value}\n' for key, value in destination.items()))
            if target.read_bytes() != original:
                raise config.ConfigurationError('Bootstrap changed during migration; source has been preserved')
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    print(json.dumps({'summary': {'credentials': len(plans), 'applied': apply,
                                 'legacy_secrets_retained': True}}), flush=True)


def _parameter(config, bootstrap, name, reader, *, decrypt=True):
    try:
        return reader(bootstrap, 'ssm', 'get-parameter', payload={
            'Name': name, 'WithDecryption': decrypt,
        })['Parameter']
    except config.StoreError as error:
        if error.code != 'ParameterNotFound':
            raise
        return None


def _verify_standard(config, bootstrap, name, reader):
    response = reader(bootstrap, 'ssm', 'describe-parameters', payload={
        'ParameterFilters': [{'Key': 'Name', 'Option': 'Equals', 'Values': [name]}],
    })
    records = response.get('Parameters', [])
    if (len(records) != 1 or records[0].get('Name') != name
            or records[0].get('Tier') != 'Standard' or records[0].get('Type') != 'SecureString'
            or response.get('NextToken')):
        raise config.ConfigurationError('Encrypted parameter must use Standard tier: ' + name)


def _tags(values):
    return [{'Key': key, 'Value': value} for key, value in sorted(values.items())]


def _verify_tags(config, bootstrap, name, expected, reader):
    response = reader(bootstrap, 'ssm', 'list-tags-for-resource', payload={
        'ResourceType': 'Parameter', 'ResourceId': name,
    })
    actual = {tag['Key']: tag['Value'] for tag in response.get('TagList', [])}
    if any(actual.get(key) != value for key, value in expected.items()):
        raise config.ConfigurationError('Parameter tag readback differs: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default=os.environ.get('PROD_ENV_FILE', '/production/.env.prod'))
    parser.add_argument('--apply', action='store_true', help='Copy and verify values; default: read-only preview')
    args = parser.parse_args()
    os.environ['TELEGRAM_LOG_LEVEL'] = 'OFF'
    import production_config as config
    from production_lock import production_lock
    try:
        with production_lock(Path(args.env_file).resolve()):
            migrate(config, args.env_file, apply=args.apply)
    except (config.ConfigurationError, OSError, KeyError):
        print('Encrypted-parameter migration failed; check AWS access and record compatibility.', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
