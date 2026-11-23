"""Application dates remain UTC even when the host uses another timezone."""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import shared_utils
from notifications import ContextFormatter, TelegramFormatter


@pytest.fixture
def non_utc_host(monkeypatch):
    try:
        with monkeypatch.context() as environment:
            environment.setenv('TZ', 'UTC+11')
            time.tzset()
            yield
    finally:
        time.tzset()


def test_calendar_and_display_dates_use_utc_at_year_boundary(non_utc_host, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return cls(2025, 1, 1, tzinfo=tz)

    monkeypatch.setattr(shared_utils, 'datetime', Clock)
    stamp = 1735689600  # 2025-01-01 00:00 UTC; host date is still December 31.
    assert shared_utils.to_datetime(stamp).tzinfo is timezone.utc
    assert shared_utils.unix_to_month_year(stamp) == 'Jan 2025'
    assert shared_utils.unix_to_full_date(stamp) == 'Jan 01'
    assert shared_utils.unix_to_full_date(stamp - 1) == 'Dec 31, 2024'
    assert shared_utils.jinja2_iso_utc(stamp * 1000) == '2025-01-01T00:00:00Z'


@pytest.mark.parametrize('formatter', [ContextFormatter, TelegramFormatter])
def test_log_timestamps_use_utc_independently_of_host(non_utc_host, formatter):
    record = logging.LogRecord('example', logging.INFO, __file__, 1, 'Example', (), None)
    record.created = 1735689600
    text = formatter('%(asctime)s %(message)s', datefmt='%Y-%m-%dT%H:%M:%SZ').format(record)
    assert '2025-01-01T00:00:00Z Example' in text
