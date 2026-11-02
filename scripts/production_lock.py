#!/usr/bin/env python3
"""Serialize production tooling from all worktrees against the shared configuration."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys


def run_locked(env_file, command):
    # Resolve links so all worktrees use the main checkout's single lock.
    production_file = Path(env_file).resolve()
    lock_path = production_file.parent / '.tmp/production-deploy.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        print('Waiting for shared production tooling lock…', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        environment = {**os.environ, 'PROD_ENV_FILE': str(production_file), 'PRODUCTION_LOCK_HELD': '1'}
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
    except OSError as error:
        print(f'Production tooling could not start: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
