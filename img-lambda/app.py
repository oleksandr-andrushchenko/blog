import io
import re
from urllib.parse import unquote_plus

import boto3
from PIL import Image

IMAGE_NAME = re.compile(
    r"^(?P<base>.+)_(?P<width>\d+)x(?P<height>\d+)\.(?P<extension>png|jpe?g)$"
)
TARGET_WIDTHS = (160, 320, 640, 1024)
s3 = boto3.client("s3")


def _webp_bytes(image):
    output = io.BytesIO()
    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image.save(output, format="WEBP", quality=82, method=6)
    return output.getvalue()


def _put_object(bucket, key, body, content_type, created):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        Metadata={"responsive-variant": "true"},
    )
    created.append(key)


def app(event, context):
    created = []
    records = event.get("Records")
    if records is None and event.get("source") == "aws.s3":
        records = [event]

    for record in records or []:
        if "detail" in record:
            bucket = record["detail"]["bucket"]["name"]
            key = unquote_plus(record["detail"]["object"]["key"])
        else:
            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(record["s3"]["object"]["key"])
        match = IMAGE_NAME.match(key.rsplit("/", 1)[-1])
        if not match:
            continue

        metadata = s3.head_object(Bucket=bucket, Key=key).get("Metadata", {})
        if metadata.get("responsive-variant") == "true":
            continue

        source_width = int(match.group("width"))
        source_height = int(match.group("height"))
        source = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        directory = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
        base = match.group("base")

        with Image.open(io.BytesIO(source)) as image:
            for target_width in TARGET_WIDTHS:
                if target_width >= source_width:
                    continue
                target_height = round(target_width * source_height / source_width)
                resized = image.copy()
                resized.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
                webp_key = f"{directory}{base}_{target_width}x{target_height}.webp"
                _put_object(bucket, webp_key, _webp_bytes(resized), "image/webp", created)

            original_webp_key = f"{directory}{base}_{source_width}x{source_height}.webp"
            _put_object(bucket, original_webp_key, _webp_bytes(image), "image/webp", created)

    return {"created": created}
