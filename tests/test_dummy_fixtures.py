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


FIXTURE_IMAGES = {
    'aedecb19-0624-4dc0-a40a-226099cca935_640x640.webp': (640, 640),
    'a7ad8a2f-a10b-42ab-949a-2f0289b51198_640x640.webp': (640, 640),
    'bf170c2e-8569-464e-bd60-52007808d166_540x960.webp': (540, 960),
    'f1f76e3c-1e14-46a5-ac93-4318e28c462c_960x540.webp': (960, 540),
}


def test_dummy_media_set_is_complete_and_valid():
    project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
    static = project_root / 'static'
    images = FIXTURE_IMAGES
    videos = {'8e0cfa67-ec77-4175-a26a-5c58f4876959.mp4', '414f4b09-0464-4df9-8909-b509f537d163.mp4'}
    assert not list(static.glob('dummy-*'))
    assert all((static / filename).is_file() for filename in images.keys() | videos)
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
            "FUNCTION_TEMPLATES_DIR": str(project_root / "api-lambda/templates"),
            "WEB_BASE_URL": os.environ['WEB_TEST_BASE_URL'],
            "API_BASE_URL": os.environ['API_TEST_BASE_URL'],
            "EMAIL_FILES_DIR": str(tmp_path / "emails"),
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        },
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    email_failures = [line for line in (result.stdout + result.stderr).splitlines() if 'email failed' in line]
    assert not email_failures, email_failures
    assert list((tmp_path / 'emails').glob('*.eml'))

    statuses = Counter()
    articles_with_images = 0
    cursor = {}
    while True:
        page = dynamodb_table.scan(**cursor)
        for item in page.get("Items", []):
            for field in ('avatar_filename', 'image_filename'):
                if item.get(field):
                    assert item[field] in FIXTURE_IMAGES, (item['pk'], field, item[field])
                    assert (project_root / 'static' / item[field]).is_file()
            if item["pk"].startswith("POST#") and item["sk"] == "META":
                statuses[item["status"]] += 1
                if '<img ' in item.get('content', ''):
                    assert item.get('image_filename'), item['pk']
                    articles_with_images += 1
        if not page.get("LastEvaluatedKey"):
            break
        cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}
    assert statuses == {"published": 75, "unpublished": 3, "rejected": 3}
    assert articles_with_images > 0
