#!/usr/bin/env python3
"""Serialize production tooling from all worktrees against the shared configuration."""
import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys


@contextmanager
def production_lock(env_file):
    # Resolve links so all worktrees share a lock outside disposable build directories.
    production_file = Path(env_file).resolve()
    if os.environ.get('PRODUCTION_LOCK_HELD') == '1' and os.environ.get('PROD_ENV_FILE') == str(production_file):
        yield dict(os.environ)
        return
    lock_path = production_file.parent / '.production-deploy.lock'
    with lock_path.open('a') as lock:
        print('Waiting for shared production tooling lock…', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield {**os.environ, 'PROD_ENV_FILE': str(production_file), 'PRODUCTION_LOCK_HELD': '1'}


def run_locked(env_file, command, *, load_config=True):
    production_file = Path(env_file).resolve()
    with production_lock(production_file) as environment:
        if load_config and production_file.exists():
            if Path(__file__).with_name('production_config.py').exists():
                from production_config import read_env, bootstrap_values, deployment_environment
                bootstrap = read_env(production_file)
                if 'PROD_CONFIG_PREFIX' in bootstrap or 'PROD_SECRETS_PREFIX' in bootstrap:
                    bootstrap = bootstrap_values(production_file)
                    if not bootstrap.get('PROD_CONFIG_PREFIX'):
                        raise ValueError('Empty production store locations')
                    environment = deployment_environment(bootstrap, environment)
            elif 'PROD_CONFIG_PREFIX=' in production_file.read_text():
                raise ValueError('This older checkout needs the production configuration bootstrap')
        # The recursive Make reads configuration only after this lock is held.
        process = subprocess.Popen(command, env=environment, start_new_session=True)
        previous = {}
        def forward(signum, _frame):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, forward)
            result = process.wait()
            return result if result >= 0 else 128 - result
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('env_file', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required')
    try:
        return run_locked(args.env_file, command)
    except (OSError, ValueError, KeyError):
        print('Production tooling could not start; check bootstrap configuration, AWS login and store permissions.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
