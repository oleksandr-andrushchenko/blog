"""Assign reviewed category illustrations; dry-run by default.

Run with the scripts-production service in docker-compose.scripts.production.yml:
    python scripts/set_category_images.py scripts/fixtures/category-images.json --user-id USER_ID
Add --apply only after reviewing the output. Existing category images are preserved.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from api_utils import get_s3_client, save_public_file, update_category
from app_config import get_static_files_dir, get_static_s3_bucket, is_prod
from basic_dtos import ImageFileDTO
from category_dtos import UpdateCategoryDTO
from shared_utils import (
    NotAuthenticatedError, NotAuthorizedError, Permission, UserBannedError,
    get_category, get_user, verify_authorization,
)


STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"


def ensure_public_image(image: ImageFileDTO) -> None:
    """Reuse the upload service and refuse to overwrite different public bytes."""
    existing = None
    if is_prod():
        try:
            response = get_s3_client().get_object(Bucket=get_static_s3_bucket(), Key=image.filename)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"NoSuchKey", "404", "NotFound"}:
                raise
        else:
            with response["Body"] as body:
                existing = body.read()
    else:
        target = Path(get_static_files_dir()) / image.filename
        if target.exists():
            existing = target.read_bytes()
    if existing is not None:
        if hashlib.sha256(existing).digest() != hashlib.sha256(image.content).digest():
            raise ValueError("Public image already exists with different content")
        return
    save_public_file(image, filename=image.filename)


def run(assignments: Path, user_id: str, apply: bool = False) -> int:
    items = json.loads(Path(assignments).read_text())
    if not isinstance(items, dict) or not items:
        raise ValueError("Assignments must be a nonempty object mapping category slugs to static filenames")
    if is_prod() and not get_static_s3_bucket():
        raise ValueError("STATIC_S3_BUCKET must be configured")
    if not is_prod() and not get_static_files_dir():
        raise ValueError("STATIC_FILES_DIR must be configured")
    user = get_user(user_id)
    verify_authorization(user, Permission.UPDATE_CATEGORY)
    totals = Counter()
    root = STATIC_ROOT.resolve()
    for slug, filename in items.items():
        record = {"input": {"slug": slug, "filename": filename}}
        try:
            if not isinstance(filename, str) or not filename or Path(filename).name != filename:
                raise ValueError("Image filename must be a single static filename")
            category = get_category(slug)
            dto = UpdateCategoryDTO(image_action="replace", image_filename=filename)
            action = "skip" if category.image_filename == filename else "update"
            if category.image_filename and action == "update":
                raise ValueError("Category already has another image; review it before replacing")
            if action == "update":
                source = (root / filename).resolve()
                if not source.is_relative_to(root):
                    raise ValueError("Image must be inside the static directory")
                image = ImageFileDTO(source.read_bytes(), filename)
                if apply:
                    ensure_public_image(image)
                    update_category(category, dto, user)
            totals[action] += 1
            record["output"] = {
                "action": action, "service": "update_category", "category_slug": slug,
                "dto": dto.get_changes(), "user_id": user_id,
                "publication_intent": "update public category illustration" if action == "update" else None,
                "apply": apply,
            }
        except (PermissionError, BotoCoreError, ClientError, NotAuthenticatedError, NotAuthorizedError, UserBannedError):
            raise
        except Exception as exc:
            totals["error"] += 1
            record["error"] = {"type": type(exc).__name__, "reason": str(exc)}
        print(json.dumps(record))
    print(json.dumps({"totals": dict(totals)}))
    return 1 if totals["error"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assignments", type=Path)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.assignments, args.user_id, args.apply))
