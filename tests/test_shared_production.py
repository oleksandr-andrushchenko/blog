"""Shared production config and locking checks; no Make or AWS commands execute."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'scripts'))
from worktree_production import configure_worktree_production, link_production_env


@pytest.fixture
def pair(tmp_path):
    source = tmp_path / 'main checkout'
    target = tmp_path / 'linked checkout'
    source.mkdir()
    target.mkdir()
    (source / '.env.prod').write_text('AWS_STACK=example-stack\nWEB_LAMBDA_CODE_TIMESTAMP=before\n')
    return source, target


def test_shared_link_observes_atomic_timestamp_updates_without_becoming_a_copy(pair):
    source, target = pair
    link_production_env(source, target)
    assert (target / '.env.prod').is_symlink()
    # Mirrors Make's resolved-path sed -i operation using only synthetic settings.
    subprocess.run(['sed', '-i.bak', 's/TIMESTAMP=before/TIMESTAMP=after/',
                    str((target / '.env.prod').resolve())], check=True)
    assert 'TIMESTAMP=after' in (source / '.env.prod').read_text()
    assert (target / '.env.prod').is_symlink()
    assert (target / '.env.prod').read_bytes() == (source / '.env.prod').read_bytes()


def test_different_private_config_is_never_discarded(pair):
    source, target = pair
    (target / '.env.prod').write_text('AWS_STACK=other-stack\n')
    with pytest.raises(ValueError, match='different .env.prod'):
        link_production_env(source, target)
    assert (target / '.env.prod').read_text() == 'AWS_STACK=other-stack\n'
    (target / '.env.prod').write_bytes((source / '.env.prod').read_bytes())
    link_production_env(source, target)
    assert (target / '.env.prod').is_symlink()


def test_dangling_link_is_consistent_when_main_config_does_not_yet_exist(pair):
    source, target = pair
    (source / '.env.prod').unlink()
    link_production_env(source, target)
    assert (target / '.env.prod').is_symlink()
    (source / '.env.prod').write_text('AWS_STACK=example-stack\n')
    assert (target / '.env.prod').read_text() == 'AWS_STACK=example-stack\n'


def test_older_checkouts_get_only_shared_production_bootstrap_support(pair):
    source, target = pair
    (source / 'scripts').mkdir()
    (source / 'scripts/production_lock.py').write_bytes((ROOT / 'scripts/production_lock.py').read_bytes())
    (target / 'Makefile').write_text('''PROD_ENV_FILE := .env.prod
AWS_STACK := $(shell sed -n "s/^AWS_STACK=//p" $(PROD_ENV_FILE))
.PHONY: help
help:
\t@echo local-help
build:
\tsed -i.bak 's/example/example/' $(PROD_ENV_FILE)
''')
    (target / 'docker-compose.scripts.production.yml').write_text('''services:
  scripts-production:
    env_file:
      - .env.prod
    volumes:
      - ./:/app
''')
    configure_worktree_production(source, target)
    first = (target / 'Makefile').read_text()
    configure_worktree_production(source, target)
    assert (target / 'Makefile').read_text() == first
    assert 'realpath -m -- "$(PROD_ENV_FILE)"' in first
    assert 'sed -n "s/^AWS_STACK=//p" "$(PROD_ENV_FILE)"' in first
    assert "sed -i.bak 's/example/example/' \"$(PROD_ENV_FILE)\"" in first
    assert '$(MAKE) -j1 PRODUCTION_LOCK_HELD=1 $(MAKECMDGOALS)' in first
    assert first.count('_PRODUCTION_LOCK_GOALS :=') == 1
    assert '@echo local-help' in first
    assert (target / 'scripts/production_lock.py').read_bytes() == (source / 'scripts/production_lock.py').read_bytes()
    composition = yaml.safe_load((target / 'docker-compose.scripts.production.yml').read_text())
    service = composition['services']['scripts-production']
    assert service['env_file'] == ['${PROD_ENV_FILE:-.env.prod}']
    assert {'type': 'bind', 'source': '${PROD_ENV_FILE:-./.env.prod}',
            'target': '/app/.env.prod', 'read_only': True} in service['volumes']


def test_lock_serializes_worktrees_and_child_reads_configuration_after_lock(pair, tmp_path):
    source, target = pair
    link_production_env(source, target)
    marker, release, output = (tmp_path / name for name in ('started', 'release', 'output'))
    worker = tmp_path / 'worker.py'
    worker.write_text('''import os, pathlib, sys, time
config, marker, release, output, role = map(pathlib.Path, sys.argv[1:])
if str(role) == 'first':
    import shutil
    shutil.rmtree(config.parent / '.tmp', ignore_errors=True)
    marker.touch()
    while not release.exists(): time.sleep(0.02)
    config.write_text('AWS_STACK=updated-example\\n')
else:
    output.write_text(config.read_text())
    assert os.environ['PRODUCTION_LOCK_HELD'] == '1'
    assert pathlib.Path(os.environ['PROD_ENV_FILE']).is_absolute()
''')
    def start(config, role):
        return subprocess.Popen([sys.executable, str(ROOT / 'scripts/production_lock.py'), str(config), '--',
                                 sys.executable, str(worker), str(config), str(marker), str(release),
                                 str(output), role], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first = start(source / '.env.prod', 'first')
    second = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        second = start(target / '.env.prod', 'second')
        assert second.stdout.readline().startswith('Waiting for shared production')
        assert second.poll() is None
        assert not output.exists()
        release.touch()
        assert first.wait(timeout=10) == 0, first.stderr.read()
        assert second.wait(timeout=10) == 0, second.stderr.read()
        assert output.read_text() == 'AWS_STACK=updated-example\n'
    finally:
        release.touch()
        for process in (first, second):
            if process is not None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def test_failed_command_preserves_exit_status_and_releases_lock(pair):
    source, _ = pair
    prefix = [sys.executable, str(ROOT / 'scripts/production_lock.py'), str(source / '.env.prod'), '--']
    failed = subprocess.run([*prefix, sys.executable, '-c', 'raise SystemExit(7)'], capture_output=True, text=True)
    assert failed.returncode == 7
    success = subprocess.run([*prefix, sys.executable, '-c', 'pass'], capture_output=True, text=True, timeout=10)
    assert success.returncode == 0
