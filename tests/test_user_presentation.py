"""Profile report prefills preserve identity, escaping, and the contact API budget."""
import os
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pyquery import PyQuery
import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))


@pytest.mark.parametrize('address', ['https://example.test/@Author?x=1&y=2',
    'https://example.test/' + 'x' * 1500])
def test_user_report_preserves_identity_and_bounds_every_reason(address):
    environment = Environment(loader=FileSystemLoader(ROOT / 'web-lambda/templates'),
                              autoescape=select_autoescape())
    environment.globals['url'] = lambda *args, **kwargs: 'https://api.example.test/contacts/message'
    name = '\"><script>alert("unsafe")</script>'
    document = PyQuery(environment.get_template('fragments/user-report.html').render(
        user=SimpleNamespace(id='user-id'), user_canonical_url=address,
        cur_user=SimpleNamespace(name=name, email='reader@example.test')))
    form = document('#user-report-form')
    resource = 'URL: ' + address if len(address) <= 750 else 'User ID: user-id'
    assert form.attr('data-report-resource') == resource
    assert form('input[name="name"]').attr('value') == name
    assert not form('script')
    assert not form('[required], [minlength], [maxlength]')
    limit = int(form.attr('data-report-details-limit'))
    assert limit > 0
    for option in form('select[name="reason"] option[value!=""]').items():
        message = f'User report\nReason: {option.attr("value")}\n{resource}\n\nDetails: {"x" * limit}'
        assert len(message) <= 1000
        assert resource in message
