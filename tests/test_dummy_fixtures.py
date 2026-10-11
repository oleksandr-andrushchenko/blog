"""Exercise the request-less CLI used by recreate-local-dynamodb."""
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from test_utils import TEST_DYNAMODB_TABLE, dynamodb_table, recreate_dynamodb_table


@pytest.mark.functional
def test_dummy_fixture_cli_completes_without_http_request(tmp_path):
    recreate_dynamodb_table()
    project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts/generate_dummy_fixtures.py")],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(str(project_root / path) for path in (
                "shared", "api-lambda", "scripts")),
            "DYNAMODB_TABLE": TEST_DYNAMODB_TABLE,
            "APP_STAGE": "local",
            "EMAIL_FILES_DIR": str(tmp_path / "emails"),
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        },
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    statuses = Counter()
    cursor = {}
    while True:
        page = dynamodb_table.scan(**cursor)
        for item in page.get("Items", []):
            if item["pk"].startswith("POST#") and item["sk"] == "META":
                statuses[item["status"]] += 1
        if not page.get("LastEvaluatedKey"):
            break
        cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}
    assert statuses == {"published": 75, "unpublished": 3, "rejected": 3}
