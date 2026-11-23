"""Website social links remain independent of account publishing connections."""
import os
from pathlib import Path
import sys

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pyquery import PyQuery as pq

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'web-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import app_config
from site_socials import website_social_links


@pytest.fixture
def settings(monkeypatch):
    values = {'contact_socials': [], 'site_telegram_channel_url': '', 'site_threads_profile_url': '', 'site_pinterest_profile_url': ''}
    for key, value in values.items():
        monkeypatch.setitem(app_config.config, key, value)
    return app_config.config


def test_website_links_use_environment_configuration(monkeypatch):
    monkeypatch.setenv('SITE_TELEGRAM_CHANNEL_URL', 'https://t.me/example_catalog')
    monkeypatch.setenv('SITE_THREADS_PROFILE_URL', 'https://www.threads.com/@example_catalog')
    config = app_config._get_live_config()
    assert config['site_telegram_channel_url'] == 'https://t.me/example_catalog'
    assert config['site_threads_profile_url'] == 'https://www.threads.com/@example_catalog'


def test_empty_links_have_no_container(settings):
    assert website_social_links() == []
    template = Environment(loader=FileSystemLoader(str(ROOT / 'web-lambda/templates')),
                           autoescape=select_autoescape()).get_template('fragments/site-social-links.html')
    assert not template.render(contact_socials=[]).strip()


def test_site_links_replace_platform_duplicates_and_preserve_other_socials(settings):
    existing = [{'icon': 'github', 'url': 'https://github.com/example'},
                {'icon': 'telegram', 'url': 'https://t.me/old_example'},
                {'icon': 'threads', 'url': 'https://www.threads.net/@old_example'}]
    settings.update(contact_socials=existing, site_telegram_channel_url=' https://t.me/example_catalog/ ',
                    site_threads_profile_url='https://www.threads.com/@example_catalog')
    links = website_social_links()
    assert links == [existing[0],
                     {'icon': 'telegram', 'name': 'Telegram', 'url': 'https://t.me/example_catalog'},
                     {'icon': 'threads', 'name': 'Threads', 'url': 'https://www.threads.com/@example_catalog'}]
    assert len(existing) == 3  # Never mutate shared JSON configuration.
    template = Environment(loader=FileSystemLoader(str(ROOT / 'web-lambda/templates')),
                           autoescape=select_autoescape()).get_template('fragments/site-social-links.html')
    document = pq(template.render(contact_socials=links))
    assert [link.text() for link in document('a').items()] == ['Github', 'Telegram', 'Threads']
    assert len(document('a[target="_blank"][rel="nofollow noopener noreferrer"]')) == 3
    assert len(document('i[aria-hidden="true"]')) == 3


@pytest.mark.parametrize('key,url', [
    ('site_telegram_channel_url', 'javascript:alert(1)'),
    ('site_telegram_channel_url', 'https://unrelated.example/channel'),
    ('site_telegram_channel_url', 'https://t.me.unrelated.example/channel'),
    ('site_telegram_channel_url', 'http://t.me/example'),
    ('site_telegram_channel_url', 'https://t.me/'),
    ('site_telegram_channel_url', 'https://user:pass@t.me/example'),
    ('site_telegram_channel_url', 'https://t.me:bad/example'),
    ('site_threads_profile_url', 'https://www.threads.com/'),
    ('site_threads_profile_url', 'https://t.me/example'),
    ('site_threads_profile_url', 'https://www.threads.com/@white space'),
])
def test_invalid_links_are_not_displayed(settings, key, url):
    settings[key] = url
    assert website_social_links() == []


def test_public_links_do_not_require_publishing_credentials(settings, monkeypatch):
    for key in ('telegram_publisher_bot_token', 'telegram_publisher_webhook_secret',
                'threads_app_id', 'threads_app_secret', 'threads_token_encryption_key'):
        monkeypatch.setitem(settings, key, '')
    settings['site_telegram_channel_url'] = 'https://t.me/+example_invite'
    settings['site_threads_profile_url'] = 'https://www.threads.net/@example_catalog'
    assert len(website_social_links()) == 2


def test_deployment_maps_public_links_to_web_only():
    class Loader(yaml.SafeLoader):
        pass
    Loader.add_multi_constructor('!', lambda loader, suffix, node: {suffix: loader.construct_scalar(node)})
    template = yaml.load((ROOT / 'cf.yml').read_text(), Loader=Loader)
    web = template['Resources']['WebFunction']['Properties']['Environment']['Variables']
    for variable, parameter in [('SITE_TELEGRAM_CHANNEL_URL', 'SiteTelegramChannelUrl'),
                                ('SITE_THREADS_PROFILE_URL', 'SiteThreadsProfileUrl'),
                                ('SITE_PINTEREST_PROFILE_URL', 'SitePinterestProfileUrl')]:
        assert template['Parameters'][parameter]['Default'] == ''
        assert web[variable] == {'Ref': parameter}
        for name, resource in template['Resources'].items():
            if resource['Type'] == 'AWS::Lambda::Function' and name != 'WebFunction':
                assert variable not in resource['Properties'].get('Environment', {}).get('Variables', {})


def test_public_pinterest_profile_link_needs_no_publishing_app(settings):
    settings['site_pinterest_profile_url'] = 'https://www.pinterest.com/example_catalog/'
    assert website_social_links() == [{'icon': 'pinterest', 'name': 'Pinterest',
                                      'url': 'https://www.pinterest.com/example_catalog'}]
