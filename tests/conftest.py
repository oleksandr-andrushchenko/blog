import os
from pathlib import Path

import pytest


@pytest.fixture(scope='session', autouse=True)
def no_unmocked_external_services():
    if os.environ.get('TEST_EXTERNAL_CALL_LOG'):
        import sitecustomize
        assert hasattr(sitecustomize, 'guard'), 'Test network guard was not installed before imports'
    yield
    path = os.environ.get('TEST_EXTERNAL_CALL_LOG')
    if path:
        attempts = Path(path).read_text().strip()
        assert not attempts, 'Unmocked external calls were blocked (including Lambda processes):\n' + attempts


def pytest_itemcollected(item):
    # Show only the test function name (and param if any)
    item._nodeid = item.nodeid.split("::test_")[-1]
