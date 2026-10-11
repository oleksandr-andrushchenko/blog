"""Image formats must survive validation, resizing, storage, and thumbnail generation."""
import importlib.util
import os
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(project_root / "shared"))
sys.path.insert(0, str(project_root / "api-lambda"))

import api_utils
from basic_dtos import ImageFileDTO


def image_bytes(format, size=(1280, 800)):
    output = BytesIO()
    Image.new("RGB", size, "navy").save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize("format,extension", [
    ("JPEG", "jpg"), ("PNG", "png"), ("GIF", "gif"),
    ("WEBP", "webp"), ("AVIF", "avif"),
])
def test_supported_images_resize_and_keep_their_format(format, extension):
    dto = ImageFileDTO(image_bytes(format), "upload.bin")
    assert dto.extension == extension
    assert api_utils.get_image_dimensions(dto.content) == (1280, 800)
    resized = api_utils.resize_public_image(dto)
    assert resized.extension == extension
    assert api_utils.get_image_dimensions(resized.content) == (1200, 750)


def test_non_image_upload_remains_rejected():
    with pytest.raises(ValueError, match="Invalid image type"):
        ImageFileDTO(b"not an image", "pretend.avif")


@pytest.mark.parametrize("format,extension", [("WEBP", "webp"), ("AVIF", "avif")])
def test_modern_image_storage_uses_dimensions_and_image_mime_type(monkeypatch, format, extension):
    s3 = Mock()
    monkeypatch.setattr(api_utils, "is_prod", lambda: True)
    monkeypatch.setattr(api_utils, "get_s3_client", lambda: s3)
    monkeypatch.setattr(api_utils, "get_static_s3_bucket", lambda: "test-bucket")
    content = image_bytes(format, (128, 80))
    filename = api_utils.save_public_file(ImageFileDTO(content, f"source.{extension}"))
    assert filename.endswith(f"_128x80.{extension}")
    args, kwargs = s3.upload_fileobj.call_args
    assert args[0].getvalue() == content
    assert args[1:] == ("test-bucket", filename)
    assert kwargs["ExtraArgs"]["ContentType"] == f"image/{extension}"


def test_avif_generates_webp_variants(monkeypatch):
    s3 = Mock()
    s3.head_object.return_value = {"Metadata": {}}
    s3.get_object.return_value = {"Body": BytesIO(image_bytes("AVIF"))}
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: s3)
    path = project_root / "img-lambda/app.py"
    spec = importlib.util.spec_from_file_location("image_variants_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.app({"source": "aws.s3", "detail": {
        "bucket": {"name": "test-bucket"},
        "object": {"key": "sample_1280x800.avif"},
    }}, None)
    assert result["created"] == [
        "sample_160x100.webp", "sample_320x200.webp", "sample_640x400.webp",
        "sample_1024x640.webp", "sample_1280x800.webp",
    ]
    for call in s3.put_object.call_args_list:
        assert call.kwargs["ContentType"] == "image/webp"
        assert call.kwargs["Metadata"] == {"responsive-variant": "true"}
        with Image.open(BytesIO(call.kwargs["Body"])) as image:
            assert image.format == "WEBP"
    s3.reset_mock()
    s3.head_object.return_value = {"Metadata": {"responsive-variant": "true"}}
    module.app({"Records": [{"s3": {
        "bucket": {"name": "test-bucket"}, "object": {"key": "sample_1280x800.avif"},
    }}]}, None)
    s3.get_object.assert_not_called()
    s3.put_object.assert_not_called()
