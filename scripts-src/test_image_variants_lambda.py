import importlib.util
import io
import os
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from PIL import Image


os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

handler_path = Path(__file__).parents[1] / "image-variants-lambda" / "handler.py"
spec = importlib.util.spec_from_file_location("image_variants_handler", handler_path)
image_variants_handler = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(image_variants_handler)


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_calls = []

    def head_object(self, *, Bucket, Key):
        try:
            obj = self.objects[Key]
        except KeyError:
            error = ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}},
                "HeadObject",
            )
            raise error
        return {"Metadata": obj["metadata"]}

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key]["body"])}

    def put_object(self, *, Bucket, Key, Body, ContentType, Metadata):
        body = Body if isinstance(Body, bytes) else Body.read()
        self.objects[Key] = {
            "body": body,
            "metadata": Metadata,
            "content_type": ContentType,
        }
        self.put_calls.append(Key)


def event_for(key, bucket="test-bucket"):
    return {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "detail": {
            "bucket": {"name": bucket},
            "object": {"key": key},
        },
    }


def png_bytes(width, height):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def fake_s3(monkeypatch):
    client = FakeS3()
    monkeypatch.setattr(image_variants_handler, "s3", client)
    return client


def test_creates_responsive_variants_with_preserved_aspect_ratio(fake_s3):
    source = "image_1125x784.png"
    fake_s3.objects[source] = {"body": png_bytes(1125, 784), "metadata": {}}

    result = image_variants_handler.handler(event_for(source), None)

    assert result["created"] == [
        "image_160x112.png",
        "image_320x223.png",
        "image_640x446.png",
        "image_1024x714.png",
    ]
    for filename, expected_size in (
        ("image_160x112.png", (160, 112)),
        ("image_320x223.png", (320, 223)),
        ("image_640x446.png", (640, 446)),
        ("image_1024x714.png", (1024, 714)),
    ):
        with Image.open(io.BytesIO(fake_s3.objects[filename]["body"])) as image:
            assert image.size == expected_size
        assert fake_s3.objects[filename]["metadata"] == {"responsive-variant": "true"}


def test_creates_only_a_small_variant_for_small_sources(fake_s3):
    source = "small_280x180.png"
    fake_s3.objects[source] = {"body": png_bytes(280, 180), "metadata": {}}

    result = image_variants_handler.handler(event_for(source), None)

    assert result == {"created": ["small_160x103.png"]}
    assert fake_s3.put_calls == ["small_160x103.png"]


def test_retry_regenerates_the_same_deterministic_variants(fake_s3):
    source = "image_800x600.png"
    fake_s3.objects[source] = {"body": png_bytes(800, 600), "metadata": {}}

    first = image_variants_handler.handler(event_for(source), None)
    second = image_variants_handler.handler(event_for(source), None)

    assert first["created"] == ["image_160x120.png", "image_320x240.png", "image_640x480.png"]
    assert second["created"] == ["image_160x120.png", "image_320x240.png", "image_640x480.png"]
    assert fake_s3.put_calls == [
        "image_160x120.png", "image_320x240.png", "image_640x480.png",
        "image_160x120.png", "image_320x240.png", "image_640x480.png",
    ]


def test_ignores_events_for_generated_variants(fake_s3):
    variant = "image_320x240.png"
    fake_s3.objects[variant] = {
        "body": png_bytes(320, 240),
        "metadata": {"responsive-variant": "true"},
    }

    result = image_variants_handler.handler(event_for(variant), None)

    assert result == {"created": []}
    assert fake_s3.put_calls == []
