import logging
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for directory in ("shared", "api-lambda"):
    sys.path.insert(0, str(ROOT / directory))
import search_engine_submissions


@pytest.mark.parametrize('logger_name', ['httpx', search_engine_submissions.logger.name])
@pytest.mark.parametrize('parameter', ['apikey', 'apiKey', 'access_token'])
def test_search_request_logs_redact_url_credentials(caplog, logger_name, parameter):
    with caplog.at_level(logging.INFO, logger=logger_name):
        logging.getLogger(logger_name).info(
            'HTTP Request: %s %s', 'POST',
            f'https://example.com/SubmitFeed?{parameter}=dummy-credential&format=json',
        )
    assert 'dummy-credential' not in caplog.text
    assert f'{parameter}=[REDACTED]&format=json' in caplog.text
    assert 'POST https://example.com/SubmitFeed' in caplog.text
