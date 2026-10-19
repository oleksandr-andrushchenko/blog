"""Reject corrupt text before it reaches persistence while preserving Unicode."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for directory in ("shared", "api-lambda"):
    sys.path.insert(0, str(project_root / directory))

import api_utils
from article_dtos import (
    ArticleCommentDTO, ArticleDTO, UpdateArticleCommentDTO, UpdateArticleStatusDTO, UpdateTagDTO,
)
from category_dtos import UpdateCategoryDTO


@pytest.mark.parametrize("text", ["broken\ufffdtext", "broken\ud800text", "broken\x00text", "broken\x1ftext"])
@pytest.mark.parametrize("field", ["content", "comment", "comment_update", "rejection", "tag_name", "category_name", "category_description"])
def test_mutations_reject_malformed_catalog_text(field, text):
    with pytest.raises(ValueError, match="invalid encoding or control"):
        if field == "content":
            ArticleDTO(title="Valid title", content=text * 600, tags=["valid"], category="other")
        elif field == "comment":
            ArticleCommentDTO(text=text)
        elif field == "comment_update":
            UpdateArticleCommentDTO(text=text)
        elif field == "rejection":
            UpdateArticleStatusDTO(status="rejected", comment=text)
        elif field == "tag_name":
            UpdateTagDTO(name=text)
        elif field == "category_name":
            UpdateCategoryDTO(name=text)
        else:
            UpdateCategoryDTO(description=text)


def test_category_creation_validates_before_persistence(monkeypatch):
    monkeypatch.setattr(api_utils, "verify_authorization", lambda *args: True)
    monkeypatch.setattr(api_utils, "find_category", lambda *args: pytest.fail("No database read for invalid text"))
    with pytest.raises(ValueError, match="invalid encoding or control"):
        api_utils.create_category("other", "Valid", "Invalid\x00description", SimpleNamespace(status="active"))


def test_unicode_and_multiline_content_remain_supported():
    text = "Café — Привіт 世界\nSecond line\twith spacing"
    assert ArticleCommentDTO(text=text).text == text
    assert UpdateCategoryDTO(name="Бази даних", description=text).description == text
    assert UpdateTagDTO(name="Café").name == "Café"
    assert ArticleDTO(title="Привіт 世界", content=text * 200, tags=["café"], category="other").title == "Привіт 世界"
