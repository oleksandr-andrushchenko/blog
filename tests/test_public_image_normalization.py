"""Image repairs use generated names, preserve shared files, and resume without duplicate uploads."""

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
from PIL import Image
import pytest

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for directory in ("shared", "api-lambda", "scripts"):
    sys.path.insert(0, str(project_root / directory))

import api_utils
from category_dtos import UpdateCategoryDTO
import app_config
import normalize_public_images as migration


@pytest.mark.parametrize("preserve", [False, True])
def test_category_image_replacement_can_preserve_shared_previous_image(monkeypatch, preserve):
    category = SimpleNamespace(
        slug="cloud-computing",
        name="Cloud computing",
        description="Cloud computing skills",
        image_filename="shared.webp",
        published_articles_count=2,
    )
    actor = SimpleNamespace(id="category-editor", status="active")
    monkeypatch.setattr(api_utils, "verify_authorization", Mock())
    monkeypatch.setattr(api_utils, "get_dynamodb_item", Mock(return_value={"created_at": 1}))
    monkeypatch.setattr(api_utils, "add_dynamodb_update_transact", Mock())
    monkeypatch.setattr(api_utils, "dynamodb_transact_write", Mock())
    delete = Mock()
    monkeypatch.setattr(api_utils, "drop_public_file", delete)
    api_utils.update_category(
        category,
        UpdateCategoryDTO(image_action="replace", image_filename="new.webp"),
        actor,
        preserve_previous_image=preserve,
    )
    assert category.image_filename == "new.webp"
    assert delete.call_count == int(not preserve)


def prepare_run(tmp_path, monkeypatch):
    output = BytesIO()
    Image.new("RGB", (8, 6), "blue").save(output, format="PNG")
    content = output.getvalue()
    (tmp_path / "source.png").write_bytes(content)
    item = dict(
        old_filename="old.png",
        path="source.png",
        sha256=hashlib.sha256(content).hexdigest(),
        references=[dict(kind="category", id="cloud-computing")],
    )
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(dict(images=[item])))
    entity = SimpleNamespace(image_filename="old.png")
    monkeypatch.setattr(migration, "require_user", Mock())
    monkeypatch.setattr(migration, "get_reference", lambda reference: entity)
    update = Mock(side_effect=lambda reference, filename, actor: setattr(entity, "image_filename", filename))
    monkeypatch.setattr(migration, "set_reference", update)
    s3 = Mock()
    monkeypatch.setattr(api_utils, "get_s3_client", lambda: s3)
    monkeypatch.setattr(app_config, "get_static_s3_bucket", lambda: "bucket")
    upload = Mock(return_value="a39e4a59-187e-4391-aaf6-32bfa1c297ee_8x6.png")
    monkeypatch.setattr(api_utils, "save_public_file", upload)
    monkeypatch.setattr(migration, "read_remote", Mock(return_value=content))
    ready = Mock()
    monkeypatch.setattr(migration, "wait_for_variants", ready)
    return plan, entity, update, upload, ready


def test_dry_run_does_not_upload_update_or_checkpoint(tmp_path, monkeypatch):
    plan, entity, update, upload, ready = prepare_run(tmp_path, monkeypatch)
    checkpoint = tmp_path / "progress.json"
    assert migration.run(plan, tmp_path, "owner", checkpoint) == 0
    upload.assert_not_called()
    update.assert_not_called()
    ready.assert_not_called()
    assert not checkpoint.exists()
    assert entity.image_filename == "old.png"


def test_generated_upload_and_resume_reuse_the_same_image(tmp_path, monkeypatch):
    plan, entity, update, upload, ready = prepare_run(tmp_path, monkeypatch)
    checkpoint = tmp_path / "progress.json"
    assert migration.run(plan, tmp_path, "owner", checkpoint, apply=True) == 0
    assert len(upload.call_args.args) == 1
    assert upload.call_args.kwargs == {}
    assert upload.call_args.args[0].__class__.__name__ == "ImageFileDTO"
    assert entity.image_filename == upload.return_value
    assert migration.run(plan, tmp_path, "owner", checkpoint, apply=True) == 0
    assert upload.call_count == update.call_count == 1
    assert ready.call_count == 1


def test_concurrent_reference_change_is_not_overwritten(tmp_path, monkeypatch):
    plan, entity, update, upload, ready = prepare_run(tmp_path, monkeypatch)
    entity.image_filename = "author-selected.png"
    assert migration.run(plan, tmp_path, "owner", tmp_path / "progress.json", apply=True) == 1
    upload.assert_not_called()
    update.assert_not_called()


def test_missing_variants_stop_before_reference_changes_and_preserve_upload_checkpoint(tmp_path, monkeypatch):
    plan, entity, update, upload, ready = prepare_run(tmp_path, monkeypatch)
    ready.side_effect = TimeoutError("Variants unavailable")
    checkpoint = tmp_path / "progress.json"
    with pytest.raises(TimeoutError):
        migration.run(plan, tmp_path, "owner", checkpoint, apply=True)
    update.assert_not_called()
    assert entity.image_filename == "old.png"
    assert len(json.loads(checkpoint.read_text())["uploads"]) == 1


def test_variant_check_requires_every_expected_size():
    s3 = Mock()
    filename = "a39e4a59-187e-4391-aaf6-32bfa1c297ee_320x200.png"
    migration.wait_for_variants(s3, "bucket", filename)
    keys = {call.kwargs["Key"] for call in s3.head_object.call_args_list}
    assert keys == {filename.replace("320x200.png", "160x100.webp"), filename.replace(".png", ".webp")}
    s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    with pytest.raises(TimeoutError):
        migration.wait_for_variants(s3, "bucket", filename, timeout=0)


def test_upload_only_prepares_files_without_exposing_unready_variants(tmp_path, monkeypatch):
    plan, entity, update, upload, ready = prepare_run(tmp_path, monkeypatch)
    checkpoint = tmp_path / "progress.json"
    assert migration.run(plan, tmp_path, "owner", checkpoint, apply=True, upload_only=True) == 0
    upload.assert_called_once()
    ready.assert_not_called()
    update.assert_not_called()
    assert entity.image_filename == "old.png"
    assert json.loads(checkpoint.read_text())["completed"] == {}
    assert migration.run(plan, tmp_path, "owner", checkpoint, apply=True) == 0
    assert upload.call_count == 1
    update.assert_called_once()
    ready.assert_called_once()
