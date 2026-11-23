"""Provider fakes must replace transport; real test services stay reachable."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import yaml

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'tests/support'))
from network_guard import NetworkGuard, UnmockedExternalService


@pytest.mark.parametrize('host,port', [
    ('api-lambda', 5000), ('web-lambda', 5000), ('dynamodb', 8000),
    ('localhost', 12345), ('127.0.0.1', 12345), ('::1', 12345),
])
def test_only_local_test_services_are_allowed(host, port):
    assert NetworkGuard(lambda attempt: pytest.fail(str(attempt))).allowed(host, port)


@pytest.mark.parametrize('host,port', [
    ('api.openai.com', 443), ('api.telegram.org', 443), ('api.pinterest.com', 443),
    ('graph.threads.net', 443), ('s3.us-west-2.amazonaws.com', 443),
    ('169.254.169.254', 80), ('10.0.0.99', 8000), ('dynamodb', 443),
    ('provider.example', None), ('127.0.0.1.provider.example', 443),
])
def test_unmocked_providers_and_unrelated_private_hosts_are_rejected(host, port):
    attempts = []
    guard = NetworkGuard(attempts.append)
    with pytest.raises(UnmockedExternalService):
        guard.check(host, port)
    assert attempts[0]['host'] == host


def test_resolved_service_addresses_are_scoped_to_their_test_port():
    guard = NetworkGuard(lambda attempt: None)
    original = lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.2', 8000))]
    guard.resolve(original, 'dynamodb', 8000)
    assert guard.allowed('10.0.0.2', 8000)
    assert not guard.allowed('10.0.0.2', 443)
    assert not guard.allowed('10.0.0.3', 8000)


@pytest.mark.parametrize('call', [
    "import requests; requests.get('https://provider.example', timeout=1)",
    "import httpx; httpx.get('https://provider.example', timeout=1)",
    "from urllib.request import urlopen; urlopen('https://provider.example', timeout=1)",
    "import socket; socket.socket().connect(('203.0.113.1', 443))",
    "import socket; socket.socket().connect_ex(('203.0.113.1', 443))",
    "import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'test', ('203.0.113.1', 443))",
    "import boto3; boto3.client('s3', aws_access_key_id='dummy', aws_secret_access_key='dummy').list_buckets()",
])
def test_guard_loads_before_clients_in_child_processes(tmp_path, call):
    # Deliberate violations use a separate log; they must never reach the network.
    log = tmp_path / 'deliberate-blocks.jsonl'
    environment = {**os.environ, 'TEST_EXTERNAL_CALL_LOG': str(log)}
    code = f"""
try:
    {call}
except Exception as error:
    chain = []
    while error is not None:
        chain.append(type(error).__name__)
        error = error.__cause__ or error.__context__
    assert 'UnmockedExternalService' in chain, chain
else:
    raise AssertionError('External transport was allowed')
"""
    result = subprocess.run([sys.executable, '-c', code], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(log.read_text().splitlines()[0])['port'] in (80, 443)


def test_all_python_test_services_install_guard_and_network_has_no_public_egress():
    compose = yaml.safe_load((ROOT / 'docker-compose.test.yml').read_text())
    assert compose['networks']['default']['internal'] is True
    for name in ('tests', 'api-lambda', 'web-lambda'):
        service = compose['services'][name]
        assert service['environment']['PYTHONPATH'].split(':')[0] == '/test-support'
        assert service['environment']['TEST_EXTERNAL_CALL_LOG'] == '/test-audit/external-calls.jsonl'
        assert './tests/support:/test-support:ro' in service['volumes']
        assert './.tmp/test-audit:/test-audit' in service['volumes']
