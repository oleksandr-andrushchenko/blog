import importlib.util
import io
import os
from pathlib import Path

import pytest
from PIL import Image
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

app_path = Path(__file__).parents[1] / "img-lambda" / "app.py"
spec = importlib.util.spec_from_file_location("image_lambda_app", app_path)
image_lambda_app = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(image_lambda_app)


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
    monkeypatch.setattr(image_lambda_app, "s3", client)
    return client


def test_creates_responsive_variants_with_preserved_aspect_ratio(fake_s3):
    source = "image_1125x784.png"
    fake_s3.objects[source] = {"body": png_bytes(1125, 784), "metadata": {}}

    result = image_lambda_app.app(event_for(source), None)

    assert result["created"] == [
        "image_160x112.webp", "image_320x223.webp",
        "image_640x446.webp", "image_1024x714.webp",
        "image_1125x784.webp",
    ]
    for filename, expected_size in (
            ("image_160x112.webp", (160, 112)),
            ("image_320x223.webp", (320, 223)),
            ("image_640x446.webp", (640, 446)),
            ("image_1024x714.webp", (1024, 714)),
            ("image_1125x784.webp", (1125, 784)),
    ):
        with Image.open(io.BytesIO(fake_s3.objects[filename]["body"])) as image:
            assert image.size == expected_size
        assert fake_s3.objects[filename]["metadata"] == {"responsive-variant": "true"}
        if filename.endswith(".webp"):
            assert fake_s3.objects[filename]["content_type"] == "image/webp"


def test_creates_only_a_small_variant_for_small_sources(fake_s3):
    source = "small_280x180.png"
    fake_s3.objects[source] = {"body": png_bytes(280, 180), "metadata": {}}

    result = image_lambda_app.app(event_for(source), None)

    assert result == {"created": [
        "small_160x103.webp", "small_280x180.webp",
    ]}
    assert fake_s3.put_calls == result["created"]


def test_retry_regenerates_the_same_deterministic_variants(fake_s3):
    source = "image_800x600.png"
    fake_s3.objects[source] = {"body": png_bytes(800, 600), "metadata": {}}

    first = image_lambda_app.app(event_for(source), None)
    second = image_lambda_app.app(event_for(source), None)

    expected = [
        "image_160x120.webp",
        "image_320x240.webp",
        "image_640x480.webp",
        "image_800x600.webp",
    ]
    assert first["created"] == expected
    assert second["created"] == expected
    assert fake_s3.put_calls == expected + expected


def test_ignores_events_for_generated_variants(fake_s3):
    variant = "image_320x240.png"
    fake_s3.objects[variant] = {
        "body": png_bytes(320, 240),
        "metadata": {"responsive-variant": "true"},
    }

    result = image_lambda_app.app(event_for(variant), None)

    assert result == {"created": []}
    assert fake_s3.put_calls == []
