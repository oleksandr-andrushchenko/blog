import os
import sys
from pathlib import Path

import pytest

project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
sys.path.insert(0, str(project_root / "shared"))
sys.path.insert(0, str(project_root / "api-lambda"))

import api_utils


IMAGE_FILENAME = "61677cb2-b588-45b8-8036-240e185a382f_1200x518.png"


@pytest.mark.parametrize("source", [IMAGE_FILENAME, f"/{IMAGE_FILENAME}"])
def test_find_static_image_filename_accepts_relative_static_urls(monkeypatch, source):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")

    assert api_utils.find_static_image_filename(f'<img alt="Example" src="{source}">') == IMAGE_FILENAME


def test_find_static_image_filename_accepts_configured_static_origin(monkeypatch):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")

    content = f'<img src="https://static.example.com/{IMAGE_FILENAME}" alt="Example">'

    assert api_utils.find_static_image_filename(content) == IMAGE_FILENAME


@pytest.mark.parametrize("source", [
    f"https://images.example.com/{IMAGE_FILENAME}",
    f"http://static.example.com/{IMAGE_FILENAME}",
    f"data:image/png;base64,{IMAGE_FILENAME}",
    f"/nested/{IMAGE_FILENAME}",
])
def test_find_static_image_filename_rejects_non_static_urls(monkeypatch, source):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")

    assert api_utils.find_static_image_filename(f'<img src="{source}">') is None


def test_find_static_image_filename_skips_external_image_before_static_image(monkeypatch):
    monkeypatch.setattr(api_utils, "get_static_base_url", lambda: "https://static.example.com")
    content = (
        f'<img src="https://images.example.com/{IMAGE_FILENAME}">'
        f'<img src="https://static.example.com/{IMAGE_FILENAME}">'
    )

    assert api_utils.find_static_image_filename(content) == IMAGE_FILENAME
