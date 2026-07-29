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


def handler(event, context):
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
        extension = match.group("extension")
        source = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        directory = key.rsplit("/", 1)[0] + "/" if "/" in key else ""

        with Image.open(io.BytesIO(source)) as image:
            for target_width in TARGET_WIDTHS:
                if target_width >= source_width:
                    continue
                target_height = round(target_width * source_height / source_width)
                variant = (
                    f"{directory}{match.group('base')}_{target_width}x{target_height}.{extension}"
                )
                resized = image.copy()
                resized.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                save_format = "JPEG" if extension in {"jpg", "jpeg"} else "PNG"
                save_kwargs = {"optimize": True}
                if save_format == "JPEG":
                    if resized.mode not in {"RGB", "L"}:
                        resized = resized.convert("RGB")
                    save_kwargs["quality"] = 82
                resized.save(output, format=save_format, **save_kwargs)
                s3.put_object(
                    Bucket=bucket,
                    Key=variant,
                    Body=output.getvalue(),
                    ContentType="image/jpeg" if save_format == "JPEG" else "image/png",
                    Metadata={"responsive-variant": "true"},
                )
                created.append(variant)

    return {"created": created}
