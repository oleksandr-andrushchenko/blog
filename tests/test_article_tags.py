import os
import sys
from pathlib import Path

import pytest

project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
sys.path.insert(0, str(project_root / "shared"))

previous_cwd = os.getcwd()
os.chdir(project_root / "shared")
import shared_utils
from query_dtos import ArticleTagQueryDTO, ArticleTagQueryType
os.chdir(previous_cwd)


def test_get_article_tags_delegates_latest_query(monkeypatch):
    query = ArticleTagQueryDTO(type=ArticleTagQueryType.LATEST, limit=6)
    expected = ["latest"]
    calls = []

    def get_latest_article_tags(query_dto):
        calls.append(query_dto)
        return expected

    monkeypatch.setattr(shared_utils, "get_latest_article_tags", get_latest_article_tags)

    assert shared_utils.get_article_tags(query) == expected
    assert calls == [query]


def test_get_article_tags_delegates_popular_query(monkeypatch):
    query = ArticleTagQueryDTO(type=ArticleTagQueryType.POPULAR, limit=4)
    expected = ["popular"]
    calls = []

    def get_popular_article_tags(query_dto):
        calls.append(query_dto)
        return expected

    monkeypatch.setattr(shared_utils, "get_popular_article_tags", get_popular_article_tags)

    assert shared_utils.get_article_tags(query) == expected
    assert calls == [query]


def test_get_popular_article_tags_queries_rating_index(monkeypatch):
    query = ArticleTagQueryDTO(limit=4)
    calls = []

    def query_dynamodb_items(**kwargs):
        calls.append(kwargs)
        return ["popular"]

    monkeypatch.setattr(shared_utils, "query_dynamodb_items", query_dynamodb_items)

    assert shared_utils.get_popular_article_tags(query) == ["popular"]
    assert calls[0]["query_dto"] is query
    assert calls[0]["index_name"] == "TAGS_BY_TYPE_RATING"


@pytest.mark.parametrize(("template", "grid_class"), [
    ("index.html", "row row-cols-1 row-cols-md-4 g-3"),
    ("article-tags.html", "row row-cols-1 row-cols-sm-2 row-cols-lg-6 g-3"),
])
def test_article_tag_pages_use_expected_grid_columns(template, grid_class):
    template_path = project_root / "web-lambda/templates" / template
    template_source = template_path.read_text()

    assert grid_class in template_source
    assert 'include "fragments/article-tag.html"' in template_source
