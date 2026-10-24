"""Publish branded article images while keeping source bytes out of public storage."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app_config import get_config, is_prod
from basic_dtos import FileDTO, ImageFileDTO


def watermark_enabled():
    return str(get_config().get('article_media_watermark_enabled', 'false')).lower() in ('true', '1', 'yes', 'on')


def watermark_settings():
    settings = get_config().get('article_media_watermark') or {}
    text = settings.get('text') or urlsplit(get_config().get('web_base_url') or '').hostname
    if not isinstance(text, str) or not text.strip() or len(text) > 200 or any(ord(char) < 32 for char in text):
        raise ValueError('A website domain or watermark text is required')
    position = settings.get('position', 'bottom-right')
    if position not in ('bottom-right', 'bottom-left', 'top-right', 'top-left'):
        raise ValueError('Invalid watermark position')
    opacity = float(settings.get('opacity', 0.5))
    font_size = float(settings.get('relative_font_size', 0.025))
    padding = float(settings.get('padding', 0.025))
    center = settings.get('center', False)
    if not (0 < opacity <= 1 and 0 < font_size <= 0.15 and 0 <= padding <= 0.15) or type(center) is not bool:
        raise ValueError('Invalid watermark settings')
    return {'text': text.strip(), 'position': position, 'opacity': opacity,
            'relative_font_size': font_size, 'padding': padding, 'center': center}


def _draw_mark(layer, settings, *, center=False):
    width, height = layer.size
    short_edge = min(width, height)
    padding = max(1, round(short_edge * settings['padding']))
    size = max(1, round(short_edge * settings['relative_font_size'] * (1.5 if center else 1)))
    draw = ImageDraw.Draw(layer)
    while True:
        font = ImageFont.load_default(size=size)
        stroke = max(1, size // 12) if size > 3 else 0
        bounds = draw.textbbox((0, 0), settings['text'], font=font, stroke_width=stroke)
        text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if size == 1 or (text_width <= width - 2 * padding and text_height <= height - 2 * padding):
            break
        size -= 1
    if center:
        x, y = (width - text_width) // 2, (height - text_height) // 2
    else:
        x = width - padding - text_width if settings['position'].endswith('right') else padding
        y = height - padding - text_height if settings['position'].startswith('bottom') else padding
    alpha = round(255 * settings['opacity'] * (0.25 if center else 1))
    draw.text((x - bounds[0], y - bounds[1]), settings['text'], font=font, fill=(255, 255, 255, alpha),
              stroke_width=stroke, stroke_fill=(0, 0, 0, alpha))


def _save_original(file_dto, public_filename):
    if not is_prod():
        directory = Path(get_config()['media_originals_dir'])
        if directory.resolve().is_relative_to(Path(get_config()['static_files_dir']).resolve()):
            raise ValueError('Media originals must be stored outside the public static directory')
        directory.mkdir(parents=True, exist_ok=True)
        (directory / public_filename).write_bytes(file_dto.content)
        return
    bucket = get_config().get('media_originals_s3_bucket')
    if not bucket or bucket == get_config().get('static_s3_bucket'):
        raise ValueError('A separate private media originals bucket is required')
    from api_utils import get_s3_client
    get_s3_client().upload_fileobj(BytesIO(file_dto.content), bucket, public_filename,
                                   ExtraArgs={'ContentType': 'application/octet-stream'})


def save_article_image(file_dto: ImageFileDTO, filename=None, *, original: FileDTO | None = None,
                      resize_unmarked=False):
    from api_utils import resize_public_image, save_public_file

    if not watermark_enabled():
        return save_public_file(resize_public_image(file_dto) if resize_unmarked else file_dto, filename)
    if filename and (Path(filename).name != filename or filename.startswith('.')):
        raise ValueError('Invalid article image filename')
    settings = watermark_settings()
    with Image.open(BytesIO(file_dto.content)) as source:
        # Never flatten animation or change GIF files as part of static watermarking.
        if source.format not in ('JPEG', 'PNG', 'WEBP', 'AVIF') or getattr(source, 'is_animated', False):
            return save_public_file(file_dto, filename)
        image = ImageOps.exif_transpose(source)
        if image.width > 1200:
            image = image.resize((1200, max(1, round(image.height * 1200 / image.width))), Image.Resampling.LANCZOS)
        base = image.convert('RGBA')
        overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
        if settings['center']:
            _draw_mark(overlay, settings, center=True)
        _draw_mark(overlay, settings)
        result = Image.alpha_composite(base, overlay)
        if source.format == 'JPEG' or ('A' not in image.getbands() and 'transparency' not in image.info):
            result = result.convert('RGB')
        output = BytesIO()
        options = {'quality': 90} if source.format in ('JPEG', 'WEBP', 'AVIF') else {}
        result.save(output, format=source.format, **options)
        public_file = replace(file_dto, content=output.getvalue())
        filename = filename or f'{uuid.uuid4()}_{result.width}x{result.height}.{public_file.extension}'
    # Fail before publishing if retaining the original fails. Nothing unmarked is made public.
    _save_original(original if original is not None else file_dto, filename)
    return save_public_file(public_file, filename)
