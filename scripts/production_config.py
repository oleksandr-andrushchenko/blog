#!/usr/bin/env python3
"""Production-only configuration bootstrap. Never imported by Lambda applications."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


# Explicit classification prevents a newly added credential from silently entering SSM.
SECRET_KEYS = frozenset({
    'APP_SECRET', 'AUTH_JWT_SECRET', 'GOOGLE_OAUTH_CLIENT_SECRET',
    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_PUBLISHER_BOT_TOKEN', 'TELEGRAM_PUBLISHER_WEBHOOK_SECRET',
    'THREADS_APP_SECRET', 'THREADS_TOKEN_ENCRYPTION_KEY',
    'LINKEDIN_APP_SECRET', 'LINKEDIN_TOKEN_ENCRYPTION_KEY',
    'BING_WEBMASTER_API_KEY', 'YANDEX_WEBMASTER_OAUTH_TOKEN', 'PEXELS_API_KEY', 'OPENAI_API_KEY',
})
CONFIG_KEYS = frozenset({
    'AWS_OWNER', 'APP_STAGE', 'APP_ENV', 'APP_DEBUG', 'DOMAIN_NAME', 'HOSTED_ZONE_ID',
    'NOTIFICATION_EMAIL', 'NOTIFICATION_PHONE', 'GOOGLE_ANALYTICS_ID', 'INDEXNOW_KEY',
    'GOOGLE_SEARCH_CONSOLE_SITE_URL', 'YANDEX_WEBMASTER_USER_ID', 'YANDEX_WEBMASTER_HOST_ID',
    'GOOGLE_OAUTH_CLIENT_ID', 'TINYMCE_API_KEY', 'TELEGRAM_CHAT_ID', 'TELEGRAM_LOG_LEVEL',
    'THREADS_APP_ID', 'LINKEDIN_APP_ID', 'LINKEDIN_API_VERSION',
    'APPLE_CREDENTIALS_SECRET_ARN', 'FACEBOOK_CREDENTIALS_SECRET_ARN',
    'WEB_BASE_URL', 'API_BASE_URL', 'STATIC_BASE_URL', 'ALLOWED_ORIGIN',
    'DYNAMODB_TABLE', 'STATIC_S3_BUCKET', 'MEDIA_ORIGINALS_S3_BUCKET', 'MEDIA_ORIGINALS_DIR',
    'STATIC_FILES_DIR', 'EMAIL_FILES_DIR', 'BACKGROUND_FUNCTION_NAME', 'SITEMAP_FUNCTION_NAME',
    'GOOGLE_SEARCH_CONSOLE_CREDENTIALS_SECRET_ARN', 'COGNITO_DOMAIN', 'COGNITO_CLIENT_ID',
    'COGNITO_USER_POOL_ID', 'CLOUDFRONT_DISTRIBUTION_ID',
    'CLOUDFRONT_CERTIFICATE_ARN', 'API_CERTIFICATE_ARN',
})
LOCAL_KEYS = frozenset({
    'AWS_STACK', 'AWS_PROJECT', 'AWS_REGION', 'PROD_CONFIG_PREFIX', 'PROD_SECRETS_PREFIX',
    # Generated deployment state and asset cache revisions remain local, not credentials.
    'WEB_LAMBDA_CODE_TIMESTAMP', 'API_LAMBDA_CODE_TIMESTAMP', 'IMG_LAMBDA_CODE_TIMESTAMP',
    'CSS_CACHE_COUNTER', 'JS_CACHE_COUNTER',
})
DEFAULTS = {'TELEGRAM_LOG_LEVEL': 'INFO', 'LINKEDIN_API_VERSION': '202610'}


class ConfigurationError(ValueError):
    pass


class StoreError(ConfigurationError):
    def __init__(self, service, operation, code='Unknown'):
        self.code = code
        super().__init__(f'AWS {service} {operation} failed ({code}); check login and permissions')


def read_env(path):
    values = {}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch('[A-Z][A-Z0-9_]*', key):
            raise ConfigurationError(f'Invalid environment entry on line {number}')
        if key in values:
            raise ConfigurationError(f'Duplicate environment key: {key}')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[key] = value
    return values


def run_aws_cli(command, payload=None, *, payload_option='--cli-input-json'):
    if payload is None:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    # The CLI can read a parameter file more than once. A pipe such as /dev/stdin
    # is not seekable; memfd provides a seekable file backed only by memory.
    with os.fdopen(os.memfd_create('aws-configuration'), 'w+') as source:
        json.dump(payload, source)
        source.flush()
        source.seek(0)
        command = [*command, payload_option, 'file:///proc/self/fd/' + str(source.fileno())]
        return subprocess.run(command, pass_fds=(source.fileno(),),
                              capture_output=True, text=True, check=False)


def aws_json(bootstrap, *arguments, payload=None):
    """Use the existing CLI/login cache without requiring host Python dependencies.

    Values for writes travel through a memory-only descriptor, never argv. Do not relay provider output/errors,
    which could contain submitted values. The caller reports only the operation name.
    """
    import shutil
    if not shutil.which('aws'):
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
        service, operation, *options = arguments
        kwargs = dict(payload or {})
        if options:
            if len(options) != 2 or options[0] not in ('--name', '--secret-id'):
                raise ConfigurationError('Unsupported configuration request')
            kwargs['Name' if options[0] == '--name' else 'SecretId'] = options[1]
        try:
            session = boto3.Session(profile_name=bootstrap['AWS_PROJECT'], region_name=bootstrap['AWS_REGION'])
            return getattr(session.client(service), operation.replace('-', '_'))(**kwargs)
        except ClientError as error:
            code = error.response.get('Error', {}).get('Code', 'Unknown')
            raise StoreError(service, operation, code) from None
        except BotoCoreError:
            raise StoreError(service, operation) from None
    command = ['aws', '--profile', bootstrap['AWS_PROJECT'], '--region', bootstrap['AWS_REGION'],
               '--no-cli-pager', '--output', 'json', *arguments]
    result = run_aws_cli(command, payload)
    if result.returncode:
        match = re.search(
            r'\((ParameterNotFound|ResourceNotFoundException|ResourceExistsException|ParameterAlreadyExists)\)',
            result.stderr,
        )
        raise StoreError(arguments[0], arguments[1], match[1] if match else 'Unknown')
    try:
        return json.loads(result.stdout or '{}')
    except ValueError:
        raise ConfigurationError('AWS returned invalid configuration JSON') from None


def bootstrap_values(path):
    values = read_env(path)
    for key in ('AWS_PROJECT', 'AWS_REGION', 'AWS_STACK'):
        if not values.get(key):
            raise ConfigurationError(f'Missing bootstrap setting: {key}')
    configured = [bool(values.get(key)) for key in ('PROD_CONFIG_PREFIX', 'PROD_SECRETS_PREFIX')]
    if any(configured):
        if not all(configured):
            raise ConfigurationError('PROD_CONFIG_PREFIX and PROD_SECRETS_PREFIX must be configured together')
        validate_prefixes(values['PROD_CONFIG_PREFIX'], values['PROD_SECRETS_PREFIX'])
    return values


def validate_prefixes(parameter_prefix, secret_prefix):
    if (not re.fullmatch(r'/[A-Za-z0-9_./-]+/', parameter_prefix)
            or not re.fullmatch(r'[A-Za-z0-9/_+=.@-]+/', secret_prefix)):
        raise ConfigurationError('Use an absolute parameter prefix and a secret prefix, both ending in /')


def cloud_values(bootstrap, reader=aws_json):
    validate_prefixes(bootstrap['PROD_CONFIG_PREFIX'], bootstrap['PROD_SECRETS_PREFIX'])
    config, credentials = {}, {}
    # Explicit names avoid broad ListSecrets/GetParametersByPath permissions. SSM
    # supports ten names per request; every optional missing name is intentional.
    names = {bootstrap['PROD_CONFIG_PREFIX'] + key: key for key in sorted(CONFIG_KEYS)}
    ordered = list(names)
    for offset in range(0, len(ordered), 10):
        requested = ordered[offset:offset + 10]
        response = reader(bootstrap, 'ssm', 'get-parameters', payload={
            'Names': requested, 'WithDecryption': False,
        })
        seen = set()
        for parameter in response.get('Parameters', []):
            name, value = parameter['Name'], parameter['Value']
            if (name not in requested or name in seen or parameter.get('Type') != 'String'
                    or not isinstance(value, str) or not value or '\x00' in value):
                raise ConfigurationError('Invalid production parameter response')
            seen.add(name)
            config[names[name]] = value
        missing = response.get('InvalidParameters', [])
        if len(missing) != len(set(missing)) or set(missing) != set(requested) - seen:
            raise ConfigurationError('Incomplete production parameter response')
    for key in sorted(SECRET_KEYS):
        name = bootstrap['PROD_SECRETS_PREFIX'] + key
        try:
            secret = reader(bootstrap, 'secretsmanager', 'get-secret-value', '--secret-id', name)
        except StoreError as error:
            if error.code != 'ResourceNotFoundException':
                raise
            continue
        value = secret.get('SecretString')
        if not isinstance(value, str) or not value or '\x00' in value or secret.get('Name') != name:
            raise ConfigurationError(f'Invalid production secret: {key}')
        credentials[key] = value
    validate_settings(config, credentials)
    return config, credentials


def validate_settings(config, credentials):
    if not credentials.get('APP_SECRET') or not credentials.get('AUTH_JWT_SECRET'):
        raise ConfigurationError('APP_SECRET and AUTH_JWT_SECRET must be present and nonempty')
    for key in ('AWS_OWNER', 'APP_STAGE', 'APP_ENV', 'APP_DEBUG', 'DOMAIN_NAME', 'HOSTED_ZONE_ID',
                'GOOGLE_OAUTH_CLIENT_ID'):
        if not config.get(key):
            raise ConfigurationError(f'Missing production setting: {key}')
    webhook = credentials.get('TELEGRAM_PUBLISHER_WEBHOOK_SECRET')
    if webhook and not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', webhook):
        raise ConfigurationError('Invalid TELEGRAM_PUBLISHER_WEBHOOK_SECRET')
    validate_make_values(config)


def validate_make_values(values):
    # The existing Make recipes interpolate these values in shell commands. Fail
    # closed on Make/shell syntax rather than treating remote settings as code.
    for key, value in values.items():
        if re.search(r'[\s$`"\\;|&<>]', value):
            raise ConfigurationError(f'{key} is not safe for deployment command interpolation')


def deployment_environment(bootstrap, inherited, reader=aws_json):
    if not bootstrap.get('PROD_CONFIG_PREFIX'):
        return dict(inherited)  # Transitional legacy mode, until the reviewed migration is applied.
    if set(bootstrap) - LOCAL_KEYS:
        raise ConfigurationError('Cloud mode requires a bootstrap-only .env.prod; migrate local settings first')
    config, _ = cloud_values(bootstrap, reader)
    # Make needs nonsecret settings for certificate/build commands. Credentials are
    # loaded only by the deploy subcommand and never exported through Make.
    values = {**dict.fromkeys(CONFIG_KEYS | SECRET_KEYS, ''), **DEFAULTS, **config}
    validate_make_values(values)
    return {**inherited, **values, 'PROD_CONFIG_LOADED': '1'}


def script_environment(bootstrap, inherited, reader=aws_json):
    if not bootstrap.get('PROD_CONFIG_PREFIX'):
        return {**inherited, **bootstrap, 'AWS_PROFILE': bootstrap['AWS_PROJECT'],
                'AWS_DEFAULT_REGION': bootstrap['AWS_REGION']}
    if set(bootstrap) - LOCAL_KEYS:
        raise ConfigurationError('Cloud mode requires a bootstrap-only .env.prod')
    config, credentials = cloud_values(bootstrap, reader)
    # Clear inherited application configuration. Docker/host env cannot override the
    # authoritative cloud snapshot or accidentally point production jobs at local DBs.
    environment = {k: v for k, v in inherited.items() if k not in CONFIG_KEYS | SECRET_KEYS}
    environment.update({**DEFAULTS, **dict.fromkeys(SECRET_KEYS, ''), **config, **credentials,
                        **{k: v for k, v in bootstrap.items() if k in LOCAL_KEYS},
                        'AWS_PROFILE': bootstrap['AWS_PROJECT'], 'AWS_DEFAULT_REGION': bootstrap['AWS_REGION']})
    for key in ('DYNAMODB_ENDPOINT', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'):
        environment.pop(key, None)
    return environment


def migrate(path, parameter_prefix, secret_prefix, apply=False, reader=aws_json):
    values = bootstrap_values(path)
    if values.get('PROD_CONFIG_PREFIX'):
        raise ConfigurationError('Configuration is already in cloud mode')
    unknown = set(values) - LOCAL_KEYS - CONFIG_KEYS - SECRET_KEYS
    if unknown:
        raise ConfigurationError('Classify these keys before migration: ' + ', '.join(sorted(unknown)))
    validate_prefixes(parameter_prefix, secret_prefix)
    config = {k: v for k, v in values.items() if k in CONFIG_KEYS and v}
    credentials = {k: v for k, v in values.items() if k in SECRET_KEYS and v}
    validate_settings(config, credentials)
    for key, value in {**config, **credentials}.items():
        limit = 4096 if key in CONFIG_KEYS else 65536
        if len(value.encode()) > limit:
            raise ConfigurationError(f'{key} exceeds its AWS record size limit')
    bootstrap = {k: v for k, v in values.items() if k in LOCAL_KEYS}
    bootstrap.update(PROD_CONFIG_PREFIX=parameter_prefix, PROD_SECRETS_PREFIX=secret_prefix)
    for key in sorted(set(values) & (CONFIG_KEYS | SECRET_KEYS)):
        service = 'secretsmanager' if key in SECRET_KEYS else 'ssm'
        name = (secret_prefix if key in SECRET_KEYS else parameter_prefix) + key
        action = 'ensure record' if values[key] else 'skip empty'
        if apply and values[key]:
            ensure_record(values, service, name, values[key], reader)
        print(json.dumps({'input': {'file': str(path), 'key': key}, 'output': {
            'action': action, 'service': service, 'name': name, 'apply': apply,
        }}))
    if not apply:
        return
    # Verify every record before removing any local value. Missing/empty optional
    # settings are represented by absence, not sentinel strings or a JSON manifest.
    actual_config, actual_credentials = cloud_values(bootstrap, reader)
    if actual_config != config or actual_credentials != credentials:
        raise ConfigurationError('Readback differs; local production file has been preserved')
    write_bootstrap(path, bootstrap)


def write_bootstrap(path, bootstrap):
    import tempfile
    target = Path(path).resolve()
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix='.prod-bootstrap-')
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(''.join(f'{k}={v}\n' for k, v in bootstrap.items()))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def relocate(path, parameter_prefix, secret_prefix, apply=False, reader=aws_json):
    """Copy cloud records without overwriting values, then switch the bootstrap."""
    original = Path(path).read_bytes()
    bootstrap = bootstrap_values(path)
    if not bootstrap.get('PROD_CONFIG_PREFIX') or set(bootstrap) - LOCAL_KEYS:
        raise ConfigurationError('Relocation requires a bootstrap-only cloud configuration')
    validate_prefixes(parameter_prefix, secret_prefix)
    config, credentials = cloud_values(bootstrap, reader)
    target = {**bootstrap, 'PROD_CONFIG_PREFIX': parameter_prefix, 'PROD_SECRETS_PREFIX': secret_prefix}
    for key, value in sorted({**config, **credentials}.items()):
        secret = key in SECRET_KEYS
        service = 'secretsmanager' if secret else 'ssm'
        prefix_key = 'PROD_SECRETS_PREFIX' if secret else 'PROD_CONFIG_PREFIX'
        source_name, name = bootstrap[prefix_key] + key, target[prefix_key] + key
        if apply and source_name != name:
            ensure_record(bootstrap, service, name, value, reader)
        print(json.dumps({'input': {'service': service, 'name': source_name}, 'output': {
            'action': 'keep record' if source_name == name else 'ensure record',
            'name': name, 'apply': apply,
        }}))
    if not apply:
        return
    actual_config, actual_credentials = cloud_values(target, reader)
    if actual_config != config or actual_credentials != credentials:
        raise ConfigurationError('Readback differs; original bootstrap has been preserved')
    if Path(path).read_bytes() != original:
        raise ConfigurationError('Bootstrap changed during relocation; original file has been preserved')
    write_bootstrap(path, target)


def ensure_record(bootstrap, service, name, value, reader):
    # Retry interrupted migrations without overwriting a record that differs.
    if service == 'secretsmanager':
        operation, payload = 'create-secret', {'Name': name, 'SecretString': value}
        read_arguments, field = ('secretsmanager', 'get-secret-value', '--secret-id', name), 'SecretString'
    else:
        operation, payload = 'put-parameter', {'Name': name, 'Type': 'String', 'Value': value, 'Overwrite': False}
        read_arguments, field = ('ssm', 'get-parameter', '--name', name), 'Value'
    try:
        reader(bootstrap, service, operation, payload=payload)
    except StoreError as error:
        if error.code not in ('ResourceExistsException', 'ParameterAlreadyExists'):
            raise
        response = reader(bootstrap, *read_arguments)
        response = response['Parameter'] if service == 'ssm' else response
        if response[field] != value or (service == 'ssm' and response.get('Type') != 'String'):
            raise ConfigurationError('Existing record differs; local production file has been preserved') from None



CF_PARAMETERS = {
    'Owner': 'AWS_OWNER',
    'Stage': 'APP_STAGE',
    'Env': 'APP_ENV',
    'Debug': 'APP_DEBUG',
    'Secret': 'APP_SECRET',
    'DomainName': 'DOMAIN_NAME',
    'HostedZoneId': 'HOSTED_ZONE_ID',
    'TelegramBotToken': 'TELEGRAM_BOT_TOKEN',
    'ThreadsAppId': 'THREADS_APP_ID',
    'FacebookCredentialsSecretArn': 'FACEBOOK_CREDENTIALS_SECRET_ARN',
    'LinkedInAppId': 'LINKEDIN_APP_ID',
    'LinkedInAppSecret': 'LINKEDIN_APP_SECRET',
    'LinkedInTokenEncryptionKey': 'LINKEDIN_TOKEN_ENCRYPTION_KEY',
    'LinkedInApiVersion': 'LINKEDIN_API_VERSION',
    'ThreadsAppSecret': 'THREADS_APP_SECRET',
    'ThreadsTokenEncryptionKey': 'THREADS_TOKEN_ENCRYPTION_KEY',
    'TelegramPublisherBotToken': 'TELEGRAM_PUBLISHER_BOT_TOKEN',
    'TelegramPublisherWebhookSecret': 'TELEGRAM_PUBLISHER_WEBHOOK_SECRET',
    'TelegramChatId': 'TELEGRAM_CHAT_ID',
    'NotificationEmail': 'NOTIFICATION_EMAIL',
    'NotificationPhone': 'NOTIFICATION_PHONE',
    'GoogleAnalyticsId': 'GOOGLE_ANALYTICS_ID',
    'IndexNowKey': 'INDEXNOW_KEY',
    'GoogleSearchConsoleSiteUrl': 'GOOGLE_SEARCH_CONSOLE_SITE_URL',
    'BingWebmasterApiKey': 'BING_WEBMASTER_API_KEY',
    'YandexWebmasterOauthToken': 'YANDEX_WEBMASTER_OAUTH_TOKEN',
    'YandexWebmasterUserId': 'YANDEX_WEBMASTER_USER_ID',
    'YandexWebmasterHostId': 'YANDEX_WEBMASTER_HOST_ID',
    'GoogleOauthClientId': 'GOOGLE_OAUTH_CLIENT_ID',
    'GoogleOauthClientSecret': 'GOOGLE_OAUTH_CLIENT_SECRET',
    'AppleCredentialsSecretArn': 'APPLE_CREDENTIALS_SECRET_ARN',
    'TinyMceApiKey': 'TINYMCE_API_KEY',
    'CssCacheCounter': 'CSS_CACHE_COUNTER',
    'JsCacheCounter': 'JS_CACHE_COUNTER',
    'AuthJwtSecret': 'AUTH_JWT_SECRET',
    'Project': 'AWS_PROJECT',
    'TelegramLogLevel': 'TELEGRAM_LOG_LEVEL',
}


def deploy(path, certificate_arn, web_certificate_arn, reader=aws_json):
    bootstrap = bootstrap_values(path)
    if bootstrap.get('PROD_CONFIG_PREFIX'):
        settings, credentials = cloud_values(bootstrap, reader)
        values = {**DEFAULTS, **settings, **credentials, **bootstrap}
    else:
        values = {**DEFAULTS, **bootstrap}
    values.update({key: values.get(key) or default for key, default in DEFAULTS.items()})
    parameters = {key: values.get(variable, '') for key, variable in CF_PARAMETERS.items()}
    parameters.update(CertificateArn=certificate_arn, WebCertificateArn=web_certificate_arn)
    for function, prefix in (('Web', 'WEB'), ('Api', 'API'), ('Img', 'IMG')):
        timestamp = bootstrap.get(prefix + '_LAMBDA_CODE_TIMESTAMP', '')
        if not re.fullmatch(r'[0-9]{14}', timestamp):
            raise ConfigurationError(f'Missing or invalid {prefix}_LAMBDA_CODE_TIMESTAMP')
        parameters[function + 'FuncS3Key'] = prefix.lower() + '-function-' + timestamp + '.zip'
    for arn in (certificate_arn, web_certificate_arn):
        if not re.fullmatch(r'arn:[a-z-]+:acm:us-east-1:\d{12}:certificate/[A-Za-z0-9-]+', arn):
            raise ConfigurationError('Invalid CloudFront certificate ARN')
    command = ['aws', 'cloudformation', 'deploy',
               '--profile', bootstrap['AWS_PROJECT'], '--region', bootstrap['AWS_REGION'],
               '--template-file', str(Path(__file__).resolve().parents[1] / 'cf.yml'),
               '--s3-bucket', bootstrap['AWS_STACK'] + '-code', '--s3-prefix', 'cloudformation',
               '--stack-name', bootstrap['AWS_STACK'], '--capabilities', 'CAPABILITY_NAMED_IAM',
               '--no-fail-on-empty-changeset',
               '--tags', 'Project=' + bootstrap['AWS_PROJECT'], 'Owner=' + values.get('AWS_OWNER', ''),
               'Stage=' + values.get('APP_STAGE', ''), 'Region=' + bootstrap['AWS_REGION']]
    payload = [{'ParameterKey': key, 'ParameterValue': value} for key, value in parameters.items()]
    # Capture both streams because provider validation errors may echo secret values.
    result = run_aws_cli(command, payload, payload_option='--parameter-overrides')
    if result.returncode:
        raise ConfigurationError('CloudFormation deployment failed; inspect stack events (provider output redacted)')
    print('CloudFormation deployment completed successfully (or no changes were needed).')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default=os.environ.get('PROD_ENV_FILE', '/production/.env.prod'))
    subparsers = parser.add_subparsers(dest='mode', required=True)
    run = subparsers.add_parser('run', help='Resolve credentials into process memory, then run a production script')
    run.add_argument('command', nargs=argparse.REMAINDER)
    deployment = subparsers.add_parser('deploy', help='Deploy current values without exposing them in argv')
    deployment.add_argument('--certificate-arn', required=True)
    deployment.add_argument('--web-certificate-arn', required=True)
    migration = subparsers.add_parser('migrate', help='Review a local-to-AWS migration (read-only by default)')
    migration.add_argument('--parameter-prefix', required=True)
    migration.add_argument('--secret-prefix', required=True)
    migration.add_argument('--apply', action='store_true')
    relocation = subparsers.add_parser('relocate', help='Copy cloud records to new prefixes (read-only by default)')
    relocation.add_argument('--parameter-prefix', required=True)
    relocation.add_argument('--secret-prefix', required=True)
    relocation.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.mode == 'deploy':
            if os.environ.get('PRODUCTION_LOCK_HELD') != '1':
                raise ConfigurationError('Run deployment through make deployment or make deploy-infra')
            return deploy(args.env_file, args.certificate_arn, args.web_certificate_arn)
        if args.mode in ('migrate', 'relocate'):
            # Take the same host-mounted lock as deployments, including dry runs.
            from production_lock import run_locked
            if os.environ.get('PRODUCTION_LOCK_HELD') != '1':
                command = [sys.executable, str(Path(__file__).resolve()), '--env-file', args.env_file,
                           args.mode, '--parameter-prefix', args.parameter_prefix, '--secret-prefix', args.secret_prefix]
                if args.apply:
                    command.append('--apply')
                return run_locked(args.env_file, command, load_config=False)
            operation = migrate if args.mode == 'migrate' else relocate
            operation(args.env_file, args.parameter_prefix, args.secret_prefix, args.apply)
            return 0
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            raise ConfigurationError('A production script command is required')
        if os.environ.get('PRODUCTION_LOCK_HELD') != '1':
            from production_lock import run_locked
            return run_locked(args.env_file, [sys.executable, str(Path(__file__).resolve()),
                              '--env-file', args.env_file, 'run', '--', *command], load_config=False)
        bootstrap = bootstrap_values(args.env_file)
        environment = script_environment(bootstrap, os.environ)
        os.execvpe(command[0], command, environment)
    except (ConfigurationError, OSError, KeyError):
        # No raw provider exception/request, CLI output, or credential values.
        print('Production configuration failed. Check bootstrap settings, store schema, AWS login and permissions.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
