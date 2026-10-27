"""Structured action context remains readable in both application log destinations."""
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'shared'))

from shared_utils import logger
from notifications import ContextFormatter, TelegramFormatter


def test_action_context_is_available_in_console_and_telegram(caplog):
    expected = {'by': 'https://example.com/@reader', 'url': 'https://example.com/@creator',
                'previous_action': 'follow', 'action': 'block'}
    logger.info('User impression changed', extra={'context': expected})
    record = caplog.records[-1]
    assert record.levelno == logging.INFO
    assert record.context == expected
    for formatter in (ContextFormatter('%(message)s'), TelegramFormatter('%(message)s')):
        rendered = formatter.format(record)
        assert json.loads(rendered.split('User impression changed ', 1)[1]) == expected


def test_console_context_is_one_line_and_excludes_unrelated_log_attributes():
    record = logging.makeLogRecord({'msg': 'Profile updated', 'levelno': logging.INFO,
                                    'context': {'changed_fields': ['about\nname']},
                                    'password': 'never-include-this'})
    rendered = ContextFormatter('%(message)s').format(record)
    assert '\n' not in rendered
    assert 'never-include-this' not in rendered
    assert json.loads(rendered.split('Profile updated ', 1)[1])['changed_fields'] == ['about\nname']


def test_logs_without_context_keep_the_existing_format():
    record = logging.makeLogRecord({'msg': 'Existing message', 'levelno': logging.INFO, 'levelname': 'INFO'})
    assert ContextFormatter('%(levelname)s %(message)s').format(record) == 'INFO Existing message'
