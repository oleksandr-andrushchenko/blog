"""Optional site fonts leave Bootstrap defaults intact when not configured."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pyquery import PyQuery as pq

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
env = Environment(loader=FileSystemLoader(ROOT / 'web-lambda/templates'),
                  autoescape=select_autoescape(('html',)))


def render(**settings):
    return env.get_template('fragments/fonts.html').render(**settings)


@pytest.mark.parametrize('settings', [
    {}, {'fonts': None}, {'fonts': {}}, {'fonts': {'body': '', 'headings': '  '}},
])
def test_unconfigured_fonts_emit_no_downloads_or_style_overrides(settings):
    assert render(**settings).strip() == ''


def test_configured_body_and_headings_load_only_the_selected_google_families():
    doc = pq(render(fonts={'body': 'Open Sans', 'headings': 'Montserrat'}))
    href = doc('link[rel="stylesheet"]').attr('href')
    query = parse_qs(urlsplit(href).query)
    assert query == {'family': ['Open Sans:wght@400;700', 'Montserrat:wght@400;700'], 'display': ['swap']}
    assert len(doc('link[rel="preconnect"]')) == 2
    style = doc('#site-fonts').text()
    assert '--bs-body-font-family: "Open Sans", var(--bs-font-sans-serif);' in style
    assert '--bs-heading-font-family: "Montserrat", var(--bs-body-font-family);' in style


@pytest.mark.parametrize('role', ['body', 'headings'])
def test_font_roles_can_be_configured_independently(role):
    doc = pq(render(fonts={role: 'Lato'}))
    query = parse_qs(urlsplit(doc('link[rel="stylesheet"]').attr('href')).query)
    assert query['family'] == ['Lato:wght@400;700']
    style = doc('#site-fonts').text()
    assert ('--bs-body-font-family:' in style) == (role == 'body')
    assert ('--bs-heading-font-family:' in style) == (role == 'headings')
    assert ('font-weight: 700' in style) == (role == 'headings')


def test_the_same_family_is_not_downloaded_twice():
    doc = pq(render(fonts={'body': ' Lato ', 'headings': 'Lato'}))
    query = parse_qs(urlsplit(doc('link[rel="stylesheet"]').attr('href')).query)
    assert query['family'] == ['Lato:wght@400;700']
