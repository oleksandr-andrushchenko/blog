#!/usr/bin/env python3
"""Create a local development worktree with independent Compose services and ports."""
import argparse
import contextlib
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from worktree_production import configure_worktree_production, link_production_env

PORT_KEYS = ('WEB_LAMBDA_PORT', 'API_LAMBDA_PORT', 'DYNAMODB_PORT')
ENV_LINE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,47}\Z')
STATE_FILE = '.tmp/worktree-setup.json'
LOCAL_KEYS = (*PORT_KEYS, 'PROD_ENV_FILE', 'COMPOSE_FILE', 'PROJECT_NAME', 'COMPOSE_PROJECT_NAME', 'WEB_BASE_URL',
              'API_BASE_URL', 'STATIC_BASE_URL', 'ALLOWED_ORIGIN', 'APP_STAGE', 'APP_ENV')


class SetupError(Exception):
    pass


def run(command, cwd, *, capture=False, env=None):
    if not capture:
        print(f'==> {shlex.join(map(str, command))}', flush=True)
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None, check=False)
    if result.returncode:
        # Captured Git/Docker preflight output contains no local environment values.
        detail = result.stderr.strip() if capture else 'see command output above'
        raise SetupError(f'{shlex.join(map(str, command))} failed ({result.returncode}): {detail}')
    return result.stdout if capture else None


def git(root, *args):
    environment = os.environ.copy()
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        environment.pop(key, None)
    return run(['git', '-C', root, *args], root, capture=True, env=environment).strip()


def worktrees(root):
    output = git(root, 'worktree', 'list', '--porcelain', '-z')
    return [Path(field.removeprefix('worktree ')) for field in output.split('\0')
            if field.startswith('worktree ')]


def env_values(text):
    values = {}
    for line in text.splitlines():
        match = ENV_LINE.match(line)
        if match:
            values[match[1]] = match[2]
    return values


def port_value(value):
    try:
        words = shlex.split(value, comments=True)
        if len(words) != 1 or not words[0].isdigit() or not 1 <= int(words[0]) <= 65535:
            raise ValueError
        return int(words[0])
    except ValueError:
        raise SetupError('Port settings must be integers between 1 and 65535') from None


def rewrite_env(text, updates):
    lines, found = [], set()
    for line in text.splitlines():
        match = ENV_LINE.match(line)
        if match and match[1] in updates:
            key = match[1]
            if key not in found:
                lines.append(f'{key}={updates[key]}')
                found.add(key)
        else:
            lines.append(line)
    lines.extend(f'{key}={value}' for key, value in updates.items() if key not in found)
    return '\n'.join(lines) + '\n'


def test_settings_file(root):
    return '.env.test.local' if (root / '.env.test.local').is_file() else '.env.test'


def assigned_ports(roots):
    occupied = set()
    for root in roots:
        for filename in ('.env', test_settings_file(root)):
            path = root / filename
            if path.is_file():
                for key, value in env_values(path.read_text()).items():
                    if key in PORT_KEYS:
                        try:
                            occupied.add(port_value(value))
                        except SetupError:
                            continue
        state_path = root / STATE_FILE
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text())
                if state.get('status') == 'configuring':
                    occupied.update(int(port) for group in state['ports'].values() for port in group.values())
            except (ValueError, KeyError, TypeError):
                pass
    return occupied


def bind_port(port):
    """Hold wildcard TCP bindings until the new worktree has registered its ports."""
    sockets = []
    try:
        for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
            if family == socket.AF_INET6 and not socket.has_ipv6:
                continue
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
            except OSError as error:
                if family == socket.AF_INET6 and error.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}:
                    continue
                raise
            sockets.append(sock)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                sock.bind((address, port))
            except OSError as error:
                if family == socket.AF_INET6 and error.errno == errno.EADDRNOTAVAIL:
                    sockets.pop().close()
                    continue
                raise
        return sockets
    except OSError:
        for sock in sockets:
            sock.close()
        return None


@contextlib.contextmanager
def allocate_ports(source, roots):
    used, held, settings = assigned_ports(roots), [], {}
    try:
        for filename in ('.env', '.env.test'):
            path = source / (test_settings_file(source) if filename == '.env.test' else filename)
            if not path.is_file():
                raise SetupError(f'Main checkout is missing {filename}')
            values = env_values(path.read_text())
            settings[filename] = {}
            for key in PORT_KEYS:
                if key not in values:
                    raise SetupError(f'Main {filename} is missing {key}')
                base = port_value(values[key])
                for port in range(base + 10, 65536, 10):
                    if port in used:
                        continue
                    sockets = bind_port(port)
                    if sockets is not None:
                        held.extend(sockets)
                        used.add(port)
                        settings[filename][key] = port
                        break
                else:
                    raise SetupError(f'No available +10 port for {filename}: {key}')
        yield settings
    finally:
        for sock in held:
            sock.close()


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(text)


def save_state(target, state):
    temporary = target / (STATE_FILE + '.new')
    write_private(temporary, json.dumps(state, indent=2) + '\n')
    temporary.replace(target / STATE_FILE)


def copy_configuration(source, target, project, ports):
    for filename, values in ports.items():
        updates = {**values, 'PROJECT_NAME': project,
                   'COMPOSE_PROJECT_NAME': project + ('-tests' if filename == '.env.test' else '')}
        if filename == '.env':
            updates['PROD_ENV_FILE'] = str((source / '.env.prod').resolve())
            updates.update(APP_STAGE='local', APP_ENV='dev', TELEGRAM_LOG_LEVEL='OFF',
                           WEB_BASE_URL=f"http://localhost:{values['WEB_LAMBDA_PORT']}",
                           API_BASE_URL=f"http://localhost:{values['API_LAMBDA_PORT']}",
                           ALLOWED_ORIGIN=f"http://localhost:{values['WEB_LAMBDA_PORT']}",
                           STATIC_BASE_URL=f"http://localhost:{values['WEB_LAMBDA_PORT']}")
        origin = source / (test_settings_file(source) if filename == '.env.test' else filename)
        destination = target / ('.env.test.local' if filename == '.env.test' else filename)
        write_private(destination, rewrite_env(origin.read_text(), updates))
    site = source / 'shared/data.json'
    if not site.is_file():
        site = target / 'shared/data.default.json'
    write_private(target / 'shared/data.json', site.read_text())
    configure_worktree_production(source, target)
    # Copy ignored media, never current tracked code or generated discovery files.
    count = 0
    for relative in git(source, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z', '--', 'static').split('\0'):
        if not relative:
            continue
        src, dst = source / relative, target / relative
        if dst.exists() or src.is_symlink() or not src.is_file() or src.name.startswith('sitemap'):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


def child_environment(target, state):
    environment = {key: value for key, value in os.environ.items() if key not in LOCAL_KEYS}
    environment['COMPOSE_PROJECT_NAME'] = state['project']
    environment['TELEGRAM_LOG_LEVEL'] = 'OFF'
    # Existing Make targets require --profile dummy; isolate it from real AWS files.
    aws_dir = target / '.tmp/worktree-aws'
    write_private(aws_dir / 'credentials', '[dummy]\naws_access_key_id=dummy\naws_secret_access_key=dummy\n')
    write_private(aws_dir / 'config', '[profile dummy]\nregion=us-west-2\n')
    environment['AWS_SHARED_CREDENTIALS_FILE'] = str(aws_dir / 'credentials')
    environment['AWS_CONFIG_FILE'] = str(aws_dir / 'config')
    return environment


def preflight(root):
    for binary in ('git', 'make', 'aws', 'jq'):
        if not shutil.which(binary):
            raise SetupError(f'{binary} is required for local setup')
    compose = ['docker-compose'] if shutil.which('docker-compose') else ['docker', 'compose']
    if not shutil.which(compose[0]):
        raise SetupError('Docker Compose is required for local setup')
    run([*compose, 'version'], root, capture=True)
    run(['docker', 'info', '--format', '{{.ServerVersion}}'], root, capture=True)
    return compose


def wait_http(url, *, dynamodb=False, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except HTTPError as error:
            if dynamodb and error.code == 400:
                return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    raise SetupError(f'Service did not become ready: {url}')


def initialize(target, state, compose):
    environment = child_environment(target, state)
    run([*compose, '-f', 'docker-compose.yml', '-p', state['project'], 'up', '-d', '--build'],
        target, env=environment)
    ports = state['ports']['.env']
    wait_http(f"http://localhost:{ports['DYNAMODB_PORT']}", dynamodb=True)
    # The existing Make target uses a fixed /tmp schema file: serialize its use
    # across setup commands in this repository without holding the lock for tests.
    common = Path(git(target, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    with (common / 'worktree-setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run(['make', 'create-local-dynamodb'], target, env=environment)
    run(['make', 'create-local-dynamodb-dummy-fixtures'], target, env=environment)
    wait_http(f"http://localhost:{ports['WEB_LAMBDA_PORT']}/")
    wait_http(f"http://localhost:{ports['API_LAMBDA_PORT']}/robots.txt")
    state['status'] = 'testing'
    save_state(target, state)
    run(['make', 'tests'], target, env=environment)
    state['status'] = 'ready'
    save_state(target, state)


def create(name, root, *, resume=False, prepare_only=False):
    if not NAME.fullmatch(name):
        raise SetupError('Use 1–48 lowercase letters, digits or hyphens; start with a letter or digit')
    roots = worktrees(root)
    if not roots:
        raise SetupError('Run this command inside a non-bare Git checkout')
    source = roots[0].resolve()
    target = source.parent / f'{source.name}-{name}'
    project = f'{source.name}-{name}'.lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
        raise SetupError('The main directory name must form a valid Compose project name')
    common = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    compose = None if prepare_only else preflight(source)
    with (common / 'worktree-setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        roots = worktrees(source)
        if resume:
            if target not in [path.resolve() for path in roots] or not (target / STATE_FILE).is_file():
                raise SetupError('No matching setup to resume')
            state = json.loads((target / STATE_FILE).read_text())
            if (state['source'] != str(source) or state['name'] != name
                    or git(target, 'branch', '--show-current') != state['branch']):
                raise SetupError('Worktree setup identity or branch has changed; refusing to overwrite it')
            link_production_env(source, target)
            if state['status'] == 'configuring':
                copy_configuration(source, target, project, state['ports'])
                state['status'] = 'configured'
                save_state(target, state)
            for filename in ('.env', '.env.test'):
                settings = env_values((target / (test_settings_file(target) if filename == '.env.test' else filename)).read_text())
                expected = project + ('-tests' if filename == '.env.test' else '')
                if settings.get('PROJECT_NAME') != project or settings.get('COMPOSE_PROJECT_NAME') != expected:
                    raise SetupError('Compose project settings changed; restore them before resuming')
                state['ports'][filename] = {key: port_value(settings[key]) for key in PORT_KEYS}
        else:
            if target.exists():
                raise SetupError(f'{target} already exists; use --resume for an interrupted setup')
            branch = f'feature/{name}'
            base = git(source, 'rev-parse', 'HEAD')
            if git(source, 'status', '--porcelain'):
                print('Main checkout has uncommitted changes; they will stay there. Using committed HEAD.', flush=True)
            with allocate_ports(source, roots) as ports:
                # Validate private config before adding a Git worktree.
                site = source / 'shared/data.json'
                if site.is_file():
                    json.loads(site.read_text())
                git(source, 'worktree', 'add', '-b', branch, str(target), base)
                state = {'source': str(source), 'name': name, 'branch': branch, 'base': base,
                         'project': project, 'ports': ports, 'status': 'configuring'}
                save_state(target, state)
                assets = copy_configuration(source, target, project, ports)
                state['status'] = 'configured'
                save_state(target, state)
                print(f'Created {target} on {branch}; copied {assets} local assets.', flush=True)
    if not prepare_only:
        initialize(target, state, compose)
    print(f"\n{'Configured' if prepare_only else 'Ready'}: {target}")
    print(f"Web: http://localhost:{state['ports']['.env']['WEB_LAMBDA_PORT']}")
    print('Use a separate browser profile: localhost cookies are shared across ports.')
    print(f'\ncd {shlex.quote(str(target))}\ncodex')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', help='Worktree suffix and feature branch name')
    parser.add_argument('--resume', action='store_true', help='Retry setup without replacing the worktree or configuration')
    parser.add_argument('--prepare-only', action='store_true', help='Create and configure without Docker, fixtures or tests')
    args = parser.parse_args(argv)
    try:
        create(args.name, Path.cwd(), resume=args.resume, prepare_only=args.prepare_only)
    except (SetupError, OSError, ValueError, KeyboardInterrupt) as error:
        print(f'Worktree setup stopped: {error}', file=sys.stderr)
        print('Any created worktree is retained. Retry the same command with --resume; no source work was removed.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
