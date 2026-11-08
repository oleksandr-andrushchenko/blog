"""Validate provenance before writes and preserve legacy article reads."""
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "shared"))

from article_dtos import ArticleDTO, UpdateArticleDTO
from shared_utils import article_from_dynamodb


SOURCE = {"url": "https://medium.com/@example/source/?ref=original", "published_at": 1767225600000}
GENERATION = {"model": "test-model", "version": "test-snapshot", "reasoning_effort": "high"}


def test_metadata_creation_and_patch_omission_and_clear_contract():
    dto = ArticleDTO(title="Original article", content="x" * 5000, category="other", tags=["example"],
                     source={**SOURCE, "url": " " + SOURCE["url"] + " "}, generation=GENERATION)
    assert dto.source == SOURCE and dto.generation == GENERATION
    assert UpdateArticleDTO(content="y" * 5000).get_changes() == {"content": "y" * 5000}
    assert UpdateArticleDTO(source=None, generation=None).get_changes() == {"source": None, "generation": None}
    assert UpdateArticleDTO(source={"url": SOURCE["url"]}).get_changes() == {"source": {"url": SOURCE["url"]}}
    assert UpdateArticleDTO(generation={"model": "test-model"}).generation == {"model": "test-model"}


@pytest.mark.parametrize("field,value", [
    ("source", []),
    ("source", {}),
    ("source", {"url": "javascript:alert(1)"}),
    ("source", {"url": "/relative/path"}),
    ("source", {"url": "https://user:password@example.com/article"}),
    ("source", {"url": "https://example.com/bad path"}),
    ("source", {"url": "https://example.com/\\bad"}),
    ("source", {"url": "https://example.com:invalid/article"}),
    ("source", {"url": "https://example.com:70000/article"}),
    ("source", {"url": "https://example.com/" + "x" * 2048}),
    ("source", {**SOURCE, "published_at": True}),
    ("source", {**SOURCE, "published_at": 1.5}),
    ("source", {**SOURCE, "published_at": "2026-01-01"}),
    ("source", {**SOURCE, "published_at": -1}),
    ("source", {**SOURCE, "published_at": 253402300800000}),
    ("source", {**SOURCE, "raw_article": "unneeded source content"}),
    ("generation", "test-model"),
    ("generation", {}),
    ("generation", {"model": " "}),
    ("generation", {"model": "x" * 201}),
    ("generation", {**GENERATION, "version": 42}),
    ("generation", {**GENERATION, "version": "x" * 201}),
    ("generation", {**GENERATION, "reasoning_effort": "x" * 65}),
    ("generation", {**GENERATION, "reasoning_effort": "high\nprivate analysis"}),
    ("generation", {**GENERATION, "reasoning": "private analysis"}),
])
def test_invalid_metadata_is_rejected_on_create_and_update(field, value):
    with pytest.raises(ValueError):
        ArticleDTO(title="Original article", content="x" * 5000, category="other", tags=["example"],
                   **{field: value})
    with pytest.raises(ValueError):
        UpdateArticleDTO(**{field: value})


def test_unknown_metadata_values_are_optional_and_caller_data_is_not_mutated():
    source = {"url": " https://example.com/source/ ", "published_at": None}
    generation = {"model": " test-model ", "version": None, "reasoning_effort": None}
    dto = UpdateArticleDTO(source=source, generation=generation)
    assert dto.source == {"url": "https://example.com/source/", "published_at": None}
    assert dto.generation == {"model": "test-model", "version": None, "reasoning_effort": None}
    assert source["url"].startswith(" ") and generation["model"].startswith(" ")


def test_article_reads_support_metadata_and_legacy_records():
    item = {"id": "provenance-test", "user_id": "owner", "title": "Original article",
            "content": "Article content", "post_slug": "original-article", "status": "unpublished",
            "rating_sk": 0, "created_at": 1}
    legacy = article_from_dynamodb(item)
    assert legacy.source is None and legacy.generation is None
    loaded = article_from_dynamodb({**item, "source": SOURCE, "generation": GENERATION})
    assert loaded.source == SOURCE and loaded.generation == GENERATION
