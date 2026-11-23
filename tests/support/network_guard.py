"""Test-only outbound network guard, installed before application imports."""
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys


LOCAL_SERVICES = {'api-lambda': 5000, 'web-lambda': 5000, 'dynamodb': 8000}


class UnmockedExternalService(RuntimeError):
    pass


class NetworkGuard:
    def __init__(self, record):
        self.record = record
        self.resolved = set()

    def allowed(self, host, port):
        if isinstance(host, bytes):
            host = host.decode('ascii')
        host = str(host).lower().rstrip('.')
        if host == 'localhost':
            return True
        try:
            if ipaddress.ip_address(host).is_loopback:
                return True
        except ValueError:
            pass
        return (host in LOCAL_SERVICES and LOCAL_SERVICES[host] == port) or (host, port) in self.resolved

    def check(self, host, port):
        if not self.allowed(host, port):
            self.record({'host': str(host), 'port': port,
                         'service': os.environ.get('TEST_SERVICE_NAME', 'tests')})
            raise UnmockedExternalService('External service must be mocked in tests: ' + str(host))

    def resolve(self, original, host, port, *args, **kwargs):
        flags = kwargs.get('flags', args[3] if len(args) > 3 else 0)
        passive = flags & socket.AI_PASSIVE and host in (None, '', '0.0.0.0', '::')
        if not passive:
            self.check(host, port)
        results = original(host, port, *args, **kwargs)
        if not passive:
            self.resolved.update((address[0], address[1]) for *_, address in results)
        return results

    def audit(self, event, args):
        if event in ('socket.connect', 'socket.sendto'):
            address = args[-1]
            # Unix-domain sockets do not contact external services.
            if isinstance(address, tuple):
                self.check(address[0], address[1])


def install():
    path = Path(os.environ['TEST_EXTERNAL_CALL_LOG'])

    def record(attempt):
        # Append one small record; all three test services share this audit file.
        with path.open('a') as stream:
            stream.write(json.dumps(attempt) + '\n')

    guard = NetworkGuard(record)
    original = socket.getaddrinfo

    def resolve(*args, **kwargs):
        return guard.resolve(original, *args, **kwargs)

    socket.getaddrinfo = resolve
    sys.addaudithook(guard.audit)
    return guard
