"""Worktree bootstrap checks use disposable Git repositories, never the project index."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'scripts'))
import create_worktree as setup


class Binding:
    def close(self):
        pass


@pytest.fixture
def repository(tmp_path, monkeypatch):
    source = tmp_path / 'catalog'
    source.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(source), '-c', 'user.name=Example',
                               '-c', 'user.email=developer@example.com', '-c', 'commit.gpgsign=false', *args],
                              check=True, capture_output=True, text=True).stdout.strip()
    git('init', '--template=', '-b', 'main')
    (source / '.gitignore').write_text('.env\n.env.prod\nshared/data.json\n.tmp/\nstatic/*\n!static/styles.css\n')
    (source / '.env.test').write_text('PROJECT_NAME=example\nWEB_LAMBDA_PORT=6010\nAPI_LAMBDA_PORT=6011\nDYNAMODB_PORT=6012\n')
    (source / 'shared').mkdir()
    (source / 'shared/data.default.json').write_text('{"site_name": "Example Site"}\n')
    (source / 'static').mkdir()
    (source / 'static/styles.css').write_text('/* committed styles */\n')
    (source / 'tracked.txt').write_text('committed\n')
    git('add', '.')
    git('commit', '-m', 'Synthetic baseline')
    (source / 'tracked.txt').write_text('parallel work stays here\n')
    (source / '.env').write_text('# Keep this comment\nPROJECT_NAME=example\nWEB_LAMBDA_PORT=5100\n'
                                 'API_LAMBDA_PORT=5101\nDYNAMODB_PORT=5102\nOPENAI_API_KEY=synthetic-secret\n')
    (source / '.env.prod').write_text('DO_NOT_COPY=production\n')
    (source / 'shared/data.json').write_text('{"site_name": "Local Example"}\n')
    (source / 'static/example.webp').write_bytes(b'synthetic-media')
    (source / 'static/sitemap.xml').write_text('do not copy old discovery files')
    monkeypatch.setattr(setup, 'bind_port', lambda port: [Binding()])
    return source


def values(root, filename='.env'):
    return setup.env_values((root / filename).read_text())


def test_prepare_preserves_source_and_committed_baseline(repository, capsys):
    before = (repository / '.env').read_bytes()
    target = setup.create('image-tools', repository, prepare_only=True)
    assert target == repository.parent / 'catalog-image-tools'
    assert setup.git(target, 'branch', '--show-current') == 'feature/image-tools'
    assert (target / 'tracked.txt').read_text() == 'committed\n'
    assert (repository / 'tracked.txt').read_text() == 'parallel work stays here\n'
    assert (repository / '.env').read_bytes() == before
    assert setup.git(repository, 'diff', '--cached') == ''
    assert values(target)['WEB_LAMBDA_PORT'] == '5110'
    assert values(target)['API_LAMBDA_PORT'] == '5111'
    assert values(target)['DYNAMODB_PORT'] == '5112'
    assert values(target, '.env.test.local')['WEB_LAMBDA_PORT'] == '6020'
    assert values(target)['PROJECT_NAME'] == values(target)['COMPOSE_PROJECT_NAME'] == 'catalog-image-tools'
    assert values(target, '.env.test.local')['COMPOSE_PROJECT_NAME'] == 'catalog-image-tools-tests'
    assert values(target)['WEB_BASE_URL'] == 'http://localhost:5110'
    assert values(target)['TELEGRAM_LOG_LEVEL'] == 'OFF'
    assert values(target)['OPENAI_API_KEY'] == 'synthetic-secret'
    assert '# Keep this comment' in (target / '.env').read_text()
    assert (target / '.env').stat().st_mode & 0o777 == 0o600
    assert json.loads((target / 'shared/data.json').read_text()) == {'site_name': 'Local Example'}
    assert (target / 'static/example.webp').read_bytes() == b'synthetic-media'
    assert (target / '.env.prod').is_symlink()
    assert (target / '.env.prod').resolve() == repository / '.env.prod'
    assert values(target)['PROD_ENV_FILE'] == str(repository / '.env.prod')
    assert (target / '.env.prod').read_bytes() == (repository / '.env.prod').read_bytes()
    assert not (target / 'static/sitemap.xml').exists()
    assert 'synthetic-secret' not in capsys.readouterr().out


def test_allocations_skip_busy_ports_and_existing_stopped_worktrees(repository, monkeypatch):
    monkeypatch.setattr(setup, 'bind_port', lambda port: None if port == 5110 else [Binding()])
    first = setup.create('first', repository, prepare_only=True)
    assert values(first)['WEB_LAMBDA_PORT'] == '5120'
    second = setup.create('second', first, prepare_only=True)
    assert values(second)['WEB_LAMBDA_PORT'] == '5130'
    assert values(second)['API_LAMBDA_PORT'] == '5121'
    assert values(second, '.env.test.local')['WEB_LAMBDA_PORT'] == '6030'
    assert setup.git(second, 'rev-parse', 'HEAD') == setup.git(repository, 'rev-parse', 'HEAD')


def test_simultaneous_creations_reserve_different_ports(repository):
    with ThreadPoolExecutor(max_workers=2) as executor:
        targets = list(executor.map(lambda name: setup.create(name, repository, prepare_only=True), ['one', 'two']))
    assigned = [int(values(target, filename)[key]) for target in targets
                for filename in ('.env', '.env.test.local') for key in setup.PORT_KEYS]
    assert len(set(assigned)) == 12


def test_resume_keeps_edits_and_does_not_allocate_again(repository):
    target = setup.create('resume', repository, prepare_only=True)
    (target / 'tracked.txt').write_text('work in the new checkout\n')
    (target / '.env').write_text((target / '.env').read_text() + 'EXTRA_SETTING=preserved\n')
    before = (target / '.env').read_bytes()
    assert setup.create('resume', repository, resume=True, prepare_only=True) == target
    assert (target / '.env').read_bytes() == before
    assert (target / 'tracked.txt').read_text() == 'work in the new checkout\n'
    with pytest.raises(setup.SetupError, match='already exists'):
        setup.create('resume', repository, prepare_only=True)


def test_missing_config_and_invalid_names_do_not_create_branches(repository):
    for name in ('../escape', 'two words', '-option', 'UPPER', 'a' * 49):
        with pytest.raises(setup.SetupError):
            setup.create(name, repository, prepare_only=True)
    (repository / '.env').unlink()
    with pytest.raises(setup.SetupError, match='missing .env'):
        setup.create('missing', repository, prepare_only=True)
    assert setup.git(repository, 'branch', '--list', 'feature/*') == ''


def test_env_rewrite_handles_quotes_duplicate_keys_and_missing_newline():
    text = '# Comment\nexport WEB_LAMBDA_PORT="5100"\nWEB_LAMBDA_PORT=5200\nTOKEN="keep # literal"'
    result = setup.rewrite_env(text, {'WEB_LAMBDA_PORT': 5110, 'PROJECT_NAME': 'example'})
    assert result.count('WEB_LAMBDA_PORT=') == 1
    assert 'TOKEN="keep # literal"\n' in result
    assert result.endswith('PROJECT_NAME=example\n')
    assert setup.port_value('"5100" # comment') == 5100
    for value in ('0', '65536', '${PORT}', '123 extra'):
        with pytest.raises(setup.SetupError):
            setup.port_value(value)


def test_real_tcp_binding_rejects_a_listening_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        assert setup.bind_port(sock.getsockname()[1]) is None


def test_full_setup_orders_commands_and_stops_on_failure(repository, monkeypatch):
    monkeypatch.setattr(setup, 'preflight', lambda root: ['docker', 'compose'])
    monkeypatch.setattr(setup, 'wait_http', lambda *args, **kwargs: None)
    original_run, calls = setup.run, []
    def run(command, cwd, *, capture=False, env=None):
        if command[0] == 'git':
            return original_run(command, cwd, capture=capture, env=env)
        calls.append(command)
        assert env['COMPOSE_PROJECT_NAME'] == 'catalog-full'
        assert env['TELEGRAM_LOG_LEVEL'] == 'OFF'
        assert 'dummy' in Path(env['AWS_SHARED_CREDENTIALS_FILE']).read_text()
        if command == ['make', 'tests']:
            raise setup.SetupError('tests failed')
    monkeypatch.setattr(setup, 'run', run)
    with pytest.raises(setup.SetupError, match='tests failed'):
        setup.create('full', repository)
    target = repository.parent / 'catalog-full'
    assert calls == [
        ['docker', 'compose', '-f', 'docker-compose.yml', '-p', 'catalog-full', 'up', '-d', '--build'],
        ['make', 'create-local-dynamodb'], ['make', 'create-local-dynamodb-dummy-fixtures'], ['make', 'tests'],
    ]
    assert json.loads((target / setup.STATE_FILE).read_text())['status'] == 'testing'
    assert target.is_dir()
    calls.clear()
    monkeypatch.setattr(setup, 'initialize', lambda *args: None)
    setup.create('full', repository, resume=True)
    assert calls == []


def test_resume_finishes_interrupted_configuration(repository, monkeypatch):
    original = setup.copy_configuration
    monkeypatch.setattr(setup, 'copy_configuration', lambda *args: (_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError, match='disk failure'):
        setup.create('partial', repository, prepare_only=True)
    target = repository.parent / 'catalog-partial'
    reserved = setup.assigned_ports(setup.worktrees(repository))
    assert {5110, 5111, 5112, 6020, 6021, 6022} <= reserved
    monkeypatch.setattr(setup, 'copy_configuration', original)
    setup.create('partial', repository, resume=True, prepare_only=True)
    assert values(target)['WEB_LAMBDA_PORT'] == '5110'
