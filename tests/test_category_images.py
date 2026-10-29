"""Reviewed image assignments are read-only by default and safe to resume."""
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for directory in ("shared", "api-lambda", "scripts"):
    sys.path.insert(0, str(project_root / directory))

import set_category_images as command
from basic_dtos import ImageFileDTO


@pytest.fixture
def assignments(tmp_path, monkeypatch):
    filename = "dummy-herbs_640x640.webp"
    (tmp_path / filename).write_bytes((project_root / "static" / filename).read_bytes())
    path = tmp_path / "assignments.json"
    path.write_text(json.dumps({"other": filename}))
    category = SimpleNamespace(image_filename=None)
    monkeypatch.setattr(command, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(command, "is_prod", lambda: False)
    monkeypatch.setattr(command, "get_static_files_dir", lambda: str(tmp_path))
    monkeypatch.setattr(command, "get_user", Mock(return_value=object()))
    monkeypatch.setattr(command, "verify_authorization", Mock())
    monkeypatch.setattr(command, "get_category", Mock(return_value=category))
    return path, category, filename


@pytest.mark.parametrize("apply", [False, True])
def test_category_images_dry_run_apply_and_rerun(assignments, monkeypatch, capsys, apply):
    path, category, filename = assignments
    upload, update = Mock(), Mock()
    monkeypatch.setattr(command, "ensure_public_image", upload)
    monkeypatch.setattr(command, "update_category", update)
    assert command.run(path, "owner", apply) == 0
    assert upload.call_count == update.call_count == int(apply)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["input"] == {"slug": "other", "filename": filename}
    assert records[0]["output"]["dto"] == {"image_action": "replace", "image_filename": filename}
    if apply:
        category.image_filename = filename
        assert command.run(path, "owner", apply) == 0
        assert upload.call_count == update.call_count == 1


def test_category_images_isolate_errors_and_preserve_existing_images(assignments, monkeypatch, capsys):
    path, _, filename = assignments
    path.write_text(json.dumps({"invalid": "../outside.png", "existing": filename, "valid": filename}))
    monkeypatch.setattr(command, "get_category", lambda slug: SimpleNamespace(
        image_filename="previous.png" if slug == "existing" else None))
    upload, update = Mock(), Mock()
    monkeypatch.setattr(command, "ensure_public_image", upload)
    monkeypatch.setattr(command, "update_category", update)
    assert command.run(path, "owner", True) == 1
    assert upload.call_count == update.call_count == 1
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["totals"] == {"error": 2, "update": 1}


def test_failed_assignment_is_retryable(assignments, monkeypatch):
    path, _, _ = assignments
    monkeypatch.setattr(command, "ensure_public_image", Mock())
    update = Mock(side_effect=[RuntimeError("interrupted"), None])
    monkeypatch.setattr(command, "update_category", update)
    assert command.run(path, "owner", True) == 1
    assert command.run(path, "owner", True) == 0
    assert update.call_count == 2


def test_category_image_permission_failures_stop_immediately(assignments, monkeypatch):
    path, _, _ = assignments
    monkeypatch.setattr(command, "ensure_public_image", Mock(side_effect=PermissionError("denied")))
    update = Mock()
    monkeypatch.setattr(command, "update_category", update)
    with pytest.raises(PermissionError):
        command.run(path, "owner", True)
    update.assert_not_called()


def test_public_image_upload_is_idempotent_and_refuses_different_bytes(monkeypatch):
    image = ImageFileDTO((project_root / "static/dummy-herbs_640x640.webp").read_bytes(), "reviewed.webp")
    monkeypatch.setattr(command, "is_prod", lambda: True)
    monkeypatch.setattr(command, "get_static_s3_bucket", lambda: "test-bucket")
    client = Mock()
    monkeypatch.setattr(command, "get_s3_client", lambda: client)
    save = Mock()
    monkeypatch.setattr(command, "save_public_file", save)
    client.get_object.return_value = {"Body": io.BytesIO(image.content)}
    command.ensure_public_image(image)
    save.assert_not_called()
    client.get_object.return_value = {"Body": io.BytesIO(b"different")}
    with pytest.raises(ValueError, match="different content"):
        command.ensure_public_image(image)
    save.assert_not_called()
    client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    command.ensure_public_image(image)
    save.assert_called_once_with(image, filename=image.filename)
