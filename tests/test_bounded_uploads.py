"""Regression coverage for the October PromptCatalog adaptations."""

import asyncio
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from PIL import Image

ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT / directory) for directory in ("shared", "api-lambda", "scripts")]

import deps
from basic_dtos import ImageFileDTO
from web import RequestValidationError


@pytest.mark.parametrize("extra", [0, 1, 5000])
def test_upload_reads_only_the_image_limit_plus_one(extra):
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    content = image.getvalue().ljust(ImageFileDTO.MAX_IMAGE_SIZE + extra, b" ")
    sizes = []

    async def read(size):
        sizes.append(size)
        return content[:size]

    async def form():
        return {"file": SimpleNamespace(filename="image.png", read=read)}

    if extra:
        with pytest.raises(RequestValidationError):
            asyncio.run(deps.get_image_file(SimpleNamespace(form=form)))
    else:
        assert asyncio.run(deps.get_image_file(SimpleNamespace(form=form))).content == content
    assert sizes == [ImageFileDTO.MAX_IMAGE_SIZE + 1]
