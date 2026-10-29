"""Exercise the request-less CLI used by recreate-local-dynamodb."""
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import filetype
import pytest
from PIL import Image

from test_utils import TEST_DYNAMODB_TABLE, dynamodb_table, recreate_dynamodb_table


def test_dummy_media_set_is_complete_and_valid():
    project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
    static = project_root / 'static'
    images = {
        'dummy-herbs_640x640.webp': (640, 640),
        'dummy-herbs-alternate_640x640.webp': (640, 640),
        'dummy-night-garden_540x960.webp': (540, 960),
        'dummy-coastal_960x540.webp': (960, 540),
    }
    videos = {'dummy-coastal.mp4', 'dummy-night-garden.mp4'}
    assert {path.name for path in static.glob('dummy-*')} == images.keys() | videos
    for filename, size in images.items():
        with Image.open(static / filename) as image:
            assert image.format == 'WEBP'
            assert image.size == size
            image.verify()
    for filename in videos:
        kind = filetype.guess(str(static / filename))
        assert kind is not None and kind.mime == 'video/mp4'


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
            for field in ('avatar_filename', 'image_filename'):
                if item.get(field):
                    assert item[field].startswith('dummy-'), (item['pk'], field, item[field])
                    assert (project_root / 'static' / item[field]).is_file()
            if item["pk"].startswith("POST#") and item["sk"] == "META":
                statuses[item["status"]] += 1
        if not page.get("LastEvaluatedKey"):
            break
        cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}
    assert statuses == {"published": 75, "unpublished": 3, "rejected": 3}
