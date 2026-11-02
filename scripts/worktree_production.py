"""Shared production configuration support for independently checked-out worktrees."""
import re
import shutil


def update_makefile(path):
    text = path.read_text()
    if 'TEST_ENV_FILE :=' not in text and 'TEST_WEB_LAMBDA_PORT :=' in text:
        text = text.replace('TEST_WEB_LAMBDA_PORT :=',
                            'TEST_ENV_FILE := $(if $(wildcard .env.test.local),.env.test.local,.env.test)\n'
                            'export TEST_ENV_FILE\nTEST_WEB_LAMBDA_PORT :=', 1)
        text = text.replace(' .env.test)', ' $(TEST_ENV_FILE))').replace('--env-file .env.test ', '--env-file $(TEST_ENV_FILE) ')
    if 'override PROD_ENV_FILE :=' not in text:
        text = text.replace('PROD_ENV_FILE := .env.prod', '''# Worktree .env.prod links point at the main checkout. Resolve before sed -i,
# which would otherwise replace the link with an independent configuration file.
PROD_ENV_FILE ?= .env.prod
override PROD_ENV_FILE := $(shell realpath -m -- "$(PROD_ENV_FILE)")
export PROD_ENV_FILE''')
    lines = []
    for line in text.splitlines():
        if ('sed ' in line or 'grep ' in line or '>> ' in line) and '$(PROD_ENV_FILE)' in line:
            line = re.sub(r'(?<!["\'])\$\(PROD_ENV_FILE\)(?!["\'])', '"$(PROD_ENV_FILE)"', line)
        lines.append(line)
    text = '\n'.join(lines) + '\n'
    if '_PRODUCTION_LOCK_GOALS' not in text:
        wrapper = '''# Acquire the shared production lock before a fresh recursive Make reads settings.
# Local services/tests keep their independent worktree configuration and ports.
_PRODUCTION_LOCK_GOALS := $(filter deploy deploy-% generate-code-files generate-%-lambda-code-files delete-% drop-cdn-cache,$(MAKECMDGOALS))
ifneq ($(_PRODUCTION_LOCK_GOALS),)
ifneq ($(PRODUCTION_LOCK_HELD),1)
_PRODUCTION_LOCK_REQUIRED := 1
endif
endif

ifeq ($(_PRODUCTION_LOCK_REQUIRED),1)
.PHONY: __production_locked $(MAKECMDGOALS)
$(MAKECMDGOALS): __production_locked ;
__production_locked:
\t@python3 scripts/production_lock.py "$(PROD_ENV_FILE)" -- $(MAKE) -j1 PRODUCTION_LOCK_HELD=1 $(MAKECMDGOALS)
else

'''
        text = text.replace('.PHONY: help\n', wrapper + '.PHONY: help\n', 1)
        text = text.rstrip() + '\n\nendif # shared production lock wrapper\n'
    path.write_text(text)


def update_compose(path):
    text = path.read_text().replace('      - .env.prod\n', '      - ${PROD_ENV_FILE:-.env.prod}\n')
    if 'target: /app/.env.prod' not in text:
        text = text.replace('      - ./:/app\n', '''      - ./:/app
      # A worktree link may point outside /app; bind the shared host file here.
      - type: bind
        source: ${PROD_ENV_FILE:-./.env.prod}
        target: /app/.env.prod
        read_only: true
''')
    path.write_text(text)


def link_production_env(source, target):
    """Keep production settings authoritative in the main checkout."""
    shared = (source / '.env.prod').resolve()
    destination = target / '.env.prod'
    if destination.is_symlink():
        if destination.resolve() != shared:
            raise ValueError('Existing .env.prod link points at a different production configuration')
        return
    if destination.exists():
        if not destination.is_file() or not shared.is_file() or destination.read_bytes() != shared.read_bytes():
            raise ValueError('A different .env.prod exists in this worktree; reconcile it with the main file first')
        destination.unlink()
    destination.symlink_to(shared)


def configure_worktree_production(source, target):
    """Install shared-config support, including when HEAD predates this tooling."""
    link_production_env(source, target)
    if (target / 'Makefile').is_file():
        update_makefile(target / 'Makefile')
    if (target / 'docker-compose.scripts.production.yml').is_file():
        update_compose(target / 'docker-compose.scripts.production.yml')
    helper = source / 'scripts/production_lock.py'
    if helper.is_file():
        destination = target / 'scripts/production_lock.py'
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(helper, destination)
