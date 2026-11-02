"""Configured publishers render together without exposing API credentials to the web."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from pyquery import PyQuery as pq

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT / directory) for directory in ('shared', 'api-lambda')]

import app_config
import shared_utils
from sitemap_handler import get_web_request


@pytest.mark.parametrize('provider, credentials', [
    ('telegram', ('telegram_publisher_bot_token', 'telegram_publisher_webhook_secret')),
    ('threads', ('threads_app_id', 'threads_app_secret', 'threads_token_encryption_key')),
    ('linkedin', ('linkedin_app_id', 'linkedin_app_secret', 'linkedin_token_encryption_key')),
])
def test_each_provider_requires_all_credentials_or_a_nonsecret_web_flag(monkeypatch, provider, credentials):
    settings = {key: 'configured' for key in credentials}
    monkeypatch.setattr(app_config, 'get_config', lambda: settings)
    assert app_config.get_publisher_enabled(provider)
    for key in credentials:
        settings[key] = ''
        assert not app_config.get_publisher_enabled(provider)
        settings[key] = 'configured'
    settings.clear()
    settings[provider + '_publisher_enabled'] = 'true'
    assert app_config.get_publisher_enabled(provider)
    settings[provider + '_publisher_enabled'] = 'false'
    assert not app_config.get_publisher_enabled(provider)


@pytest.fixture
def render(monkeypatch):
    monkeypatch.setattr(shared_utils, 'get_function_templates_dir', lambda: str(ROOT / 'web-lambda/templates'))
    environment = shared_utils.get_jinja2_env()
    user = SimpleNamespace(id='owner', status='active', has_telegram_integration=False,
                           has_threads_integration=False, has_linkedin_integration=False)
    request = get_web_request()
    from api_route_metadata import API_URL_ROUTES
    for name, path in API_URL_ROUTES.items():
        request.app.add_url_route(path, name)
    article = SimpleNamespace(id='example', owner_id='owner', status='published',
                              telegram_shared_at=None, threads_shared_at=None, linkedin_shared_at=None)

    def page(**changes):
        context = {'cur_user': user, 'article': article, 'request': request,
                   'telegram_publisher_enabled': False, 'threads_publisher_enabled': False,
                   'linkedin_publisher_enabled': False, **changes}
        return pq(environment.get_template('fragments/article-sharing.html').render(context))
    return page, user, article


def test_unconfigured_publishers_and_nonowners_have_no_panel(render):
    page, _, _ = render
    assert not page()('.article-sharing')
    assert not page(cur_user=None, telegram_publisher_enabled=True)('.article-sharing')
    assert not page(cur_user=SimpleNamespace(id='someone-else'), linkedin_publisher_enabled=True)('.article-sharing')


def test_all_configured_publishers_share_one_owner_panel(render):
    page, user, article = render
    options = {name + '_publisher_enabled': True for name in ('telegram', 'threads', 'linkedin')}
    doc = page(**options)
    assert len(doc('.article-sharing')) == 1
    for name, label in (('telegram', 'Telegram'), ('threads', 'Threads'), ('linkedin', 'LinkedIn')):
        assert doc(f'a#{name}-share').attr('aria-label') == 'Connect ' + label
        assert doc(f'a#{name}-share').attr('href').endswith('#integrations')
        setattr(user, 'has_' + name + '_integration', True)
    article.linkedin_shared_at = 123
    doc = page(**options)
    for name in ('telegram', 'threads', 'linkedin'):
        assert doc(f'button#{name}-share').attr('disabled') is None
        assert doc(f'button#{name}-share').attr('data-share-url').endswith('/share/' + name)
    assert doc('#linkedin-share').attr('data-state') == 'shared'
    article.status = 'unpublished'
    assert page(**options)('#linkedin-share').attr('disabled') is not None


def test_web_capability_flags_do_not_include_publisher_secrets():
    from test_apple_signin import template
    resources = template()['Resources']
    web = resources['WebFunction']['Properties']['Environment']['Variables']
    for provider in ('TELEGRAM', 'THREADS', 'LINKEDIN'):
        assert provider + '_PUBLISHER_ENABLED' in web
    assert not any(name in web for name in ('TELEGRAM_PUBLISHER_BOT_TOKEN', 'THREADS_APP_SECRET', 'LINKEDIN_APP_SECRET'))


def test_background_worker_shares_api_artifact_and_recovers_erasure():
    from test_apple_signin import template
    resources = template()['Resources']
    worker = resources['BackgroundFunction']['Properties']
    assert worker['Handler'] == 'background_handler.handler'
    assert worker['Code'] == resources['ApiFunction']['Properties']['Code']
    rule = resources['AccountDeletionRecoveryRule']['Properties']
    assert rule['Targets'][0]['Input'] == '{"task":"recover-account-deletions"}'
