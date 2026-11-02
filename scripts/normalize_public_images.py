"""Re-upload reviewed public images through the application helper; dry run by default."""

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import time

from botocore.exceptions import ClientError


GENERATED_IMAGE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}_(\d+)x(\d+)\.(png|jpg|jpeg|gif|webp|avif)")


def read_content(item, media_root):
    root = Path(media_root).resolve()
    path = (root / item["path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Image path must stay inside --media-root")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise ValueError("Prepared image hash differs from the reviewed plan")
    from basic_dtos import ImageFileDTO
    from api_utils import get_image_dimensions

    dto = ImageFileDTO(content, path.name)
    dimensions = get_image_dimensions(content)
    return dto, dimensions


def read_remote(s3, bucket, filename):
    try:
        with s3.get_object(Bucket=bucket, Key=filename)["Body"] as body:
            return body.read()
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def save_checkpoint(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=4) + "\n")
    temporary.replace(path)


def require_user(user_id, permission):
    from shared_utils import get_user, verify_authorization, UserBannedError
    user = get_user(user_id)
    if user.status != 'active':
        raise UserBannedError()
    verify_authorization(user, permission)
    return user


def get_reference(reference):
    if reference['kind'] != 'category':
        raise ValueError('Only category image references are supported')
    from shared_utils import get_category
    return get_category(reference['id'])


def set_reference(reference, filename, actor):
    from api_utils import update_category
    from category_dtos import UpdateCategoryDTO
    entity = get_reference(reference)
    before = asdict(entity)
    update_category(entity, UpdateCategoryDTO(image_action='replace', image_filename=filename),
                    actor, preserve_previous_image=True)
    saved = get_reference(reference)
    if saved.image_filename != filename:
        raise ValueError('Image reference did not persist')
    after = asdict(saved)
    for record in (before, after):
        for key in ('image_filename', 'updated_at'):
            record.pop(key, None)
    if before != after:
        raise ValueError('Unexpected category changes during image replacement')


def wait_for_variants(s3, bucket, filename, timeout=90):
    match = GENERATED_IMAGE.fullmatch(filename)
    if not match:
        raise ValueError("Image helper returned an unexpected filename")
    width, height, extension = int(match[1]), int(match[2]), match[3]
    if extension not in {"png", "jpg", "jpeg", "avif"}:
        return
    base = filename.rsplit("_", 1)[0]
    pending = {f"{base}_{w}x{round(w * height / width)}.webp" for w in (160, 320, 640, 1024) if w < width}
    pending.add(f"{base}_{width}x{height}.webp")
    deadline = time.monotonic() + timeout
    while pending:
        for key in list(pending):
            try:
                s3.head_object(Bucket=bucket, Key=key)
                pending.remove(key)
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
                    raise
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Responsive image variants are not ready: " + ", ".join(sorted(pending)))
        time.sleep(2)


def run(plan_path, media_root, user_id, checkpoint, apply=False, limit=None, upload_only=False):
    from app_config import get_static_s3_bucket
    from api_utils import get_s3_client, save_public_file
    from shared_utils import NotAuthenticatedError, NotAuthorizedError, Permission, UserBannedError

    if upload_only and not apply:
        raise ValueError("--upload-only requires --apply")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be positive")
    actor = require_user(user_id, Permission.UPDATE_CATEGORY)
    plan = json.loads(Path(plan_path).read_text())
    state = json.loads(Path(checkpoint).read_text()) if Path(checkpoint).exists() else {"uploads": {}, "completed": {}}
    s3, bucket = get_s3_client(), get_static_s3_bucket()
    if not bucket:
        raise ValueError("STATIC_S3_BUCKET must be configured")
    totals = Counter()
    successes = 0
    for item in plan["images"]:
        if limit is not None and successes >= limit:
            break
        try:
            uploaded = state['uploads'].get(item['sha256'])
            references = item['references']
            if apply and uploaded and references and all(
                    state['completed'].get(ref['kind'] + ':' + ref['id']) == uploaded['filename']
                    and get_reference(ref).image_filename == uploaded['filename'] for ref in references):
                totals['skipped'] += 1
                print(json.dumps({'input': item, 'output': {
                    'action': 'skip-completed', 'generated_filename': uploaded['filename'],
                    'references': references, 'apply': True}}), flush=True)
                continue
            dto, dimensions = read_content(item, media_root)
            previous = item["old_filename"]
            uploaded = state["uploads"].get(item["sha256"])
            target = uploaded["filename"] if uploaded else None
            references = item["references"]
            if not references:
                raise ValueError("A normalization item needs at least one canonical reference")
            for reference in references:
                current = get_reference(reference)
                if current.image_filename not in ({previous, target} if target else {previous}):
                    raise ValueError("Image reference changed since planning; review before proceeding")
            output = dict(
                action="reupload" if not uploaded else "resume",
                dimensions=dimensions,
                generated_filename=target,
                references=references,
                apply=apply,
                service="update_category", user_id=user_id,
                dto={"image_action": "replace", "image_filename": target},
                publication_intent="replace category illustration after upload verification",
            )
            if apply:
                if not uploaded:
                    # Never supply filename: this is the same ImageFileDTO naming path as browser uploads.
                    target = save_public_file(dto)
                    if not GENERATED_IMAGE.fullmatch(target):
                        raise ValueError("The image helper must generate a UUID and dimensions")
                    state["uploads"][item["sha256"]] = dict(filename=target, dimensions=dimensions)
                    save_checkpoint(checkpoint, state)
                remote = read_remote(s3, bucket, target)
                if remote is None or hashlib.sha256(remote).hexdigest() != item["sha256"]:
                    raise ValueError("Uploaded image bytes do not match the reviewed image")
                if upload_only:
                    output["generated_filename"] = target
                    output["action"] = "uploaded"
                    totals["uploaded"] += 1
                    successes += 1
                    print(json.dumps({"input": item, "output": output}), flush=True)
                    continue
                wait_for_variants(s3, bucket, target)
                for reference in references:
                    completion_key = reference["kind"] + ":" + reference["id"]
                    if get_reference(reference).image_filename != target:
                        set_reference(reference, target, actor)
                    state["completed"][completion_key] = target
                    save_checkpoint(checkpoint, state)
                output["generated_filename"] = target
            totals["verified" if apply else "planned"] += 1
            successes += 1
            print(json.dumps({"input": item, "output": output}), flush=True)
        except (ClientError, PermissionError, TimeoutError, NotAuthenticatedError, NotAuthorizedError, UserBannedError):
            raise
        except Exception as exc:
            totals["errors"] += 1
            print(json.dumps({"input": item, "error": {"type": type(exc).__name__, "reason": str(exc)}}), flush=True)
    print(json.dumps({"totals": dict(totals)}), flush=True)
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--media-root", required=True, type=Path)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--upload-only", action="store_true", help="Prepare uploads without changing references")
    args = parser.parse_args()
    raise SystemExit(
        run(args.plan, args.media_root, args.user_id, args.checkpoint, args.apply, args.limit, args.upload_only)
    )
