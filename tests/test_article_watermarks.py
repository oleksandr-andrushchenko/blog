"""Article watermarking keeps originals private and public derivatives consistent."""
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageChops

import api_utils
import article_image_service as service
from basic_dtos import FileDTO, ImageFileDTO

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))


def image_bytes(format='PNG', size=(800, 400), mode='RGB'):
    buffer = BytesIO()
    Image.new(mode, size, (20, 60, 90, 128) if mode == 'RGBA' else (20, 60, 90)).save(buffer, format=format)
    return buffer.getvalue()


@pytest.fixture
def storage(monkeypatch, tmp_path):
    public, private = tmp_path / 'public', tmp_path / 'private'
    public.mkdir()
    settings = {
        'article_media_watermark_enabled': 'true', 'web_base_url': 'https://catalog.example',
        'static_files_dir': str(public), 'media_originals_dir': str(private),
        'static_s3_bucket': 'example-public', 'media_originals_s3_bucket': 'example-private',
    }
    monkeypatch.setattr(service, 'get_config', lambda: settings)
    monkeypatch.setattr(service, 'is_prod', lambda: False)
    monkeypatch.setattr(api_utils, 'is_prod', lambda: False)
    monkeypatch.setattr(api_utils, 'get_static_files_dir', lambda: str(public))
    return public, private


@pytest.mark.parametrize('value', [None, False, '', 'false', '0', 'off'])
def test_watermark_flag_defaults_to_disabled_and_preserves_existing_media(monkeypatch, storage, value):
    service.get_config().pop('article_media_watermark_enabled')
    if value is not None:
        service.get_config()['article_media_watermark_enabled'] = value
    assert not service.watermark_enabled()
    content = image_bytes()
    filename = service.save_article_image(ImageFileDTO(content, 'upload.png'))
    assert (storage[0] / filename).read_bytes() == content
    assert not storage[1].exists()


@pytest.mark.parametrize('format', ['JPEG', 'PNG', 'WEBP', 'AVIF'])
def test_static_formats_are_branded_with_private_originals_and_unchanged_dimensions(storage, format):
    content = image_bytes(format)
    filename = service.save_article_image(ImageFileDTO(content, 'upload.bin'))
    assert (storage[1] / filename).read_bytes() == content
    public = (storage[0] / filename).read_bytes()
    assert public != content
    with Image.open(BytesIO(public)) as marked, Image.open(BytesIO(content)) as source:
        assert marked.format == format and marked.size == source.size == (800, 400)
        # Compare decoded pixels: a new file header alone is not a watermark.
        assert ImageChops.difference(marked.convert('RGB'), source.convert('RGB')).getbbox()
        if format == 'PNG':
            assert ImageChops.difference(marked.crop((0, 0, 400, 200)), source.crop((0, 0, 400, 200))).getbbox() is None


def test_watermark_resizes_public_copy_but_keeps_full_original(storage):
    content = image_bytes(size=(1600, 800))
    filename = service.save_article_image(ImageFileDTO(content, 'large.png'))
    assert filename.endswith('_1200x600.png')
    assert (storage[1] / filename).read_bytes() == content
    with Image.open(storage[0] / filename) as image:
        assert image.size == (1200, 600)


def test_watermark_respects_exif_orientation_before_rendering(storage):
    exif = Image.Exif()
    exif[274] = 6
    buffer = BytesIO()
    Image.new('RGB', (800, 400), 'navy').save(buffer, format='JPEG', exif=exif)
    content = buffer.getvalue()
    filename = service.save_article_image(ImageFileDTO(content, 'rotated.jpg'))
    with Image.open(storage[0] / filename) as marked:
        assert marked.size == (400, 800)
        assert marked.getexif().get(274, 1) == 1
    assert (storage[1] / filename).read_bytes() == content


@pytest.mark.parametrize('position', ['bottom-right', 'bottom-left', 'top-right', 'top-left'])
def test_watermark_position_and_optional_center_mark(storage, position):
    service.get_config()['article_media_watermark'] = {'text': 'example.test', 'position': position, 'center': True}
    content = image_bytes()
    filename = service.save_article_image(ImageFileDTO(content, 'sample.png'))
    with Image.open(storage[0] / filename) as image, Image.open(BytesIO(content)) as original:
        diff = ImageChops.difference(image, original)
        assert diff.crop((200, 100, 600, 300)).getbbox() is not None
        x = 0 if position.endswith('left') else 400
        y = 0 if position.startswith('top') else 300
        assert diff.crop((x, y, x + 400, y + 100)).getbbox() is not None


@pytest.mark.parametrize('mode', ['RGBA', 'P'])
def test_watermark_preserves_transparency(storage, mode):
    if mode == 'RGBA':
        content = image_bytes(mode=mode)
    else:
        buffer = BytesIO()
        image = Image.new('P', (800, 400), 0)
        image.save(buffer, format='PNG', transparency=0)
        content = buffer.getvalue()
    filename = service.save_article_image(ImageFileDTO(content, 'transparent.png'))
    with Image.open(storage[0] / filename) as marked, Image.open(BytesIO(content)) as source:
        assert marked.mode == 'RGBA'
        assert marked.getpixel((0, 0))[3] == source.convert('RGBA').getpixel((0, 0))[3]


@pytest.mark.parametrize('format', ['GIF', 'WEBP'])
def test_animated_images_are_preserved_without_flattening(storage, format):
    buffer = BytesIO()
    Image.new('RGB', (1280, 200), 'red').save(buffer, format=format, save_all=True,
        append_images=[Image.new('RGB', (1280, 200), 'blue')], duration=100, loop=0)
    content = buffer.getvalue()
    filename = service.save_article_image(ImageFileDTO(content, 'animation.bin'))
    assert (storage[0] / filename).read_bytes() == content
    assert not storage[1].exists()
    with Image.open(storage[0] / filename) as image:
        assert image.n_frames == 2


def test_watermark_handles_tiny_images(storage):
    filename = service.save_article_image(ImageFileDTO(image_bytes(size=(1, 1)), 'tiny.png'))
    with Image.open(storage[0] / filename) as image:
        assert image.size == (1, 1)


def test_original_storage_failure_prevents_publication(storage, monkeypatch):
    save = Mock()
    monkeypatch.setattr(api_utils, 'save_public_file', save)
    monkeypatch.setattr(service, '_save_original', Mock(side_effect=OSError('Cannot retain original')))
    with pytest.raises(OSError):
        service.save_article_image(ImageFileDTO(image_bytes(), 'sample.png'))
    save.assert_not_called()


def test_originals_cannot_be_saved_inside_public_storage(storage):
    service.get_config()['media_originals_dir'] = str(storage[0] / 'originals')
    with pytest.raises(ValueError, match='outside the public'):
        service.save_article_image(ImageFileDTO(image_bytes(), 'sample.png'))
    assert not list(storage[0].iterdir())


def test_production_keeps_originals_in_separate_bucket_and_rejects_public_bucket(storage, monkeypatch):
    monkeypatch.setattr(service, 'is_prod', lambda: True)
    s3 = Mock()
    monkeypatch.setattr(api_utils, 'get_s3_client', lambda: s3)
    original = image_bytes(size=(1600, 800))
    filename = service.save_article_image(ImageFileDTO(image_bytes(), 'prepared.png'),
                                          original=FileDTO(original, 'source.png'))
    stream, bucket, key = s3.upload_fileobj.call_args.args
    assert stream.getvalue() == original and bucket == 'example-private' and key == filename
    for bucket in (None, 'example-public'):
        service.get_config()['media_originals_s3_bucket'] = bucket
        with pytest.raises(ValueError, match='separate private'):
            service.save_article_image(ImageFileDTO(image_bytes(), 'prepared.png'))


def test_private_bucket_is_not_exposed_to_cdn_and_runtime_flag_defaults_off():
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    def intrinsic(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)
    Loader.add_multi_constructor('!', intrinsic)
    template = yaml.load((ROOT / 'cf.yml').read_text(), Loader=Loader)
    resources = template['Resources']
    bucket = resources['MediaOriginalsBucket']
    assert bucket['DeletionPolicy'] == bucket['UpdateReplacePolicy'] == 'Retain'
    assert all(bucket['Properties']['PublicAccessBlockConfiguration'].values())
    assert 'NotificationConfiguration' not in bucket['Properties']
    assert 'MediaOriginalsBucket' not in json.dumps(resources['CloudFrontDistribution'])
    assert 'MediaOriginalsBucket' not in json.dumps(resources['BucketPolicy'])
    for function, role in (('ApiFunction', 'BackendFunctionRole'),):
        environment = resources[function]['Properties']['Environment']['Variables']
        assert environment['MEDIA_ORIGINALS_S3_BUCKET'] == 'MediaOriginalsBucket'
        assert any(statement['Effect'] == 'Allow' and 's3:PutObject' in statement['Action'] and
                   statement['Resource'] == '${MediaOriginalsBucket.Arn}/*'
                   for policy in resources[role]['Properties']['Policies']
                   for statement in policy['PolicyDocument']['Statement'])
    defaults = json.loads((ROOT / 'shared/data.default.json').read_text())
    assert defaults['article_media_watermark_enabled'] is False
    assert defaults['article_media_watermark']['center'] is False
