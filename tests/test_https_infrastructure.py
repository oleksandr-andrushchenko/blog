"""Guard web edge routing, authentication forwarding and deployment isolation."""
import os
from pathlib import Path
import subprocess

import yaml


ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))


class CloudFormationLoader(yaml.SafeLoader):
    pass


CloudFormationLoader.add_multi_constructor(
    '!', lambda loader, suffix, node: {suffix: loader.construct_scalar(node)},
)


def test_web_edge_preserves_gateway_domain_and_disables_page_and_error_caching():
    template = yaml.load((ROOT / 'cf.yml').read_text(), Loader=CloudFormationLoader)
    resources = template['Resources']
    edge = resources['WebCloudFrontDistribution']
    assert edge['DependsOn'] == 'WebGatewayMapping'
    distribution = edge['Properties']['DistributionConfig']
    assert distribution['Aliases'] == [{'Ref': 'DomainName'}]
    origin, = distribution['Origins']
    # The regional endpoint remains reachable when public DNS moves to CloudFront.
    assert origin['DomainName'] == {'GetAtt': 'WebGatewayDomainName.RegionalDomainName'}
    assert origin['CustomOriginConfig']['OriginProtocolPolicy'] == 'https-only'
    behavior = distribution['DefaultCacheBehavior']
    assert behavior['TargetOriginId'] == origin['Id']
    assert behavior['ViewerProtocolPolicy'] == 'redirect-to-https'
    assert set(behavior['AllowedMethods']) == {'GET', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'POST', 'DELETE'}
    # CachingDisabled and AllViewer must stay paired: Host selects the existing
    # custom-domain mapping; cookies, Authorization and queries reach the app.
    assert behavior['CachePolicyId'] == '4135ea2d-6df8-44a3-9df3-4b5a84be39ad'
    assert behavior['OriginRequestPolicyId'] == '216adef6-5c7f-47e4-b989-5492eafa07d3'
    errors = {entry['ErrorCode']: entry['ErrorCachingMinTTL']
              for entry in distribution['CustomErrorResponses']}
    assert errors == dict.fromkeys([400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504], 0)
    assert distribution['ViewerCertificate']['AcmCertificateArn'] == {'Ref': 'WebCertificateArn'}
    assert resources['DNSRecord']['Properties']['AliasTarget'] == {
        'DNSName': {'GetAtt': 'WebCloudFrontDistribution.DomainName'},
        'HostedZoneId': 'Z2FDTNDATAQYW2',
    }
    assert resources['ApiGateway']['Properties']['DisableExecuteApiEndpoint'] is True
    assert resources['WebGatewayMapping']['Properties']['DomainName'] == {'Ref': 'WebGatewayDomainName'}
    # Static assets retain their independent cache behavior and distribution.
    static = resources['CloudFrontDistribution']['Properties']['DistributionConfig']
    assert static['Aliases'] == [{'Sub': 'static.${DomainName}'}]
    assert static['DefaultCacheBehavior']['CachePolicyId'] == {'Ref': 'S3CachePolicy'}


def test_web_certificate_is_exported_and_required_before_main_deploy(tmp_path):
    template = yaml.load((ROOT / 'cf-cert.yml').read_text(), Loader=CloudFormationLoader)
    certificate = template['Resources']['Certificate']['Properties']
    assert certificate['DomainName'] == {'Ref': 'DomainName'}
    assert certificate['ValidationMethod'] == 'DNS'
    assert certificate['DomainValidationOptions'] == [{
        'DomainName': {'Ref': 'DomainName'},
        'HostedZoneId': {'Ref': 'HostedZoneId'},
    }]
    assert template['Outputs']['WebCertificateArn']['Value'] == {'Ref': 'Certificate'}
    assert template['Outputs']['CertificateArn']['Value'] == {'Ref': 'StaticCertificate'}
    # Execute the Makefile against fake commands and synthetic configuration only.
    # A pre-upgrade certificate stack must fail before deploying the main stack.
    (tmp_path / '.env.prod').write_text('AWS_STACK=example\nAWS_PROJECT=example\nAWS_REGION=us-west-2\n'
        'WEB_LAMBDA_CODE_TIMESTAMP=20261105000000\nAPI_LAMBDA_CODE_TIMESTAMP=20261105000000\n'
        'IMG_LAMBDA_CODE_TIMESTAMP=20261105000000\n')
    (tmp_path / '.env.test').write_text('')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    (binaries / 'docker').write_text('#!/bin/sh\nexit 0\n')
    aws = binaries / 'aws'
    aws.write_text('''#!/bin/sh
case "$*" in
  *describe-stacks*WebCertificateArn*) echo "${TEST_WEB_CERT:-None}" ;;
  *describe-stacks*) echo arn:aws:acm:us-east-1:123456789012:certificate/static ;;
  *"cloudformation deploy"*) for arg in "$@"; do case "$arg" in file://*) cat "${arg#file://}" > "$TEST_DEPLOY_PAYLOAD" ;; esac; done; printf '%s\\n' "$*" > "$TEST_DEPLOY_ARGUMENTS" ;;
  *) exit 99 ;;
esac
''')
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ.get('PATH', ''))
    env.pop('TEST_WEB_CERT', None)
    env['TEST_DEPLOY_PAYLOAD'] = str(tmp_path / 'synthetic-payload.json')
    env['TEST_DEPLOY_ARGUMENTS'] = str(tmp_path / 'synthetic-arguments.txt')
    command = ['make', '--file', str(ROOT / 'Makefile'), 'PRODUCTION_LOCK_HELD=1', 'deploy-infra']
    missing = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert missing.returncode != 0
    assert 'Run make deploy-cert-infra first.' in missing.stdout
    assert 'DEPLOY ' not in missing.stdout
    env['TEST_WEB_CERT'] = 'arn:aws:acm:us-east-1:123456789012:certificate/web'
    ready = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert ready.returncode == 0, ready.stderr
    import json
    payload = {item['ParameterKey']: item['ParameterValue']
               for item in json.loads((tmp_path / 'synthetic-payload.json').read_text())}
    assert payload['WebCertificateArn'] == env['TEST_WEB_CERT']
    arguments = (tmp_path / 'synthetic-arguments.txt').read_text()
    assert '--s3-bucket example-code' in arguments
    assert '--s3-prefix cloudformation' in arguments
    assert '--parameter-overrides file:///proc/self/fd/' in arguments
