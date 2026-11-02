"""Erase upload bytes, originals and deterministic responsive variants."""
from pathlib import Path
import re

from app_config import config, is_prod


def upload_keys(filename):
    keys = {filename}
    match = re.fullmatch(r'(.+)_(\d+)x(\d+)\.(png|jpe?g|avif)', filename, re.IGNORECASE)
    if match:
        base, width, height, _ = match.groups()
        width, height = int(width), int(height)
        keys.add(f'{base}_{width}x{height}.webp')
        for size in (160, 320, 640, 1024):
            if size < width:
                keys.add(f'{base}_{size}x{round(size * height / width)}.webp')
    return keys


def erase_upload(filename):
    from account_deletion_service import _files
    # The same upload can be intentionally reused by another article/profile.
    if filename in set(_files(config)):
        return []
    # References supplied by a user are not proof of ownership of site assets.
    # Browser uploads and importers generate UUIDs or source hashes. Never erase
    # application files such as scripts.js merely because a article references one.
    identifier = r'(?:[a-f0-9]{32}|[a-f0-9]{64}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})'
    if not re.fullmatch(identifier + r'(?:_\d+x\d+)?\.[a-zA-Z0-9]+', filename):
        return []
    keys = upload_keys(filename)
    if is_prod():
        from api_utils import drop_public_file, get_s3_client
        for key in keys:
            drop_public_file(key)
        if config.get('media_originals_s3_bucket'):
            get_s3_client().delete_object(Bucket=config['media_originals_s3_bucket'], Key=filename)
    else:
        for directory, filenames in ((config['static_files_dir'], keys),
                                     (config['media_originals_dir'], {filename})):
            root = Path(directory).resolve()
            for key in filenames:
                path = (root / key).resolve()
                if path.parent != root:
                    raise ValueError('Invalid upload filename')
                path.unlink(missing_ok=True)
    return sorted(keys)
