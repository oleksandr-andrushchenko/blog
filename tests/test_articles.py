import os
import sys
from pathlib import Path
from types import SimpleNamespace

project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
sys.path.insert(0, str(project_root / "shared"))
sys.path.insert(0, str(project_root / "web-lambda"))

previous_cwd = os.getcwd()
os.chdir(project_root / "shared")
import shared_utils
import web_utils
from query_dtos import ArticleQueryDTO
os.chdir(previous_cwd)


def article(article_id, tags, rating, offset=None):
    return SimpleNamespace(
        id=article_id,
        tags=tags,
        rating=rating,
        status="published",
        offset=offset,
    )


def test_popular_articles_by_tags_continues_past_page_without_matches(monkeypatch):
    pages = {
        None: [article("global-1", ["databases"], 30, "next-page")],
        "next-page": [article("aws-1", ["aws"], 20)],
    }
    offsets = []

    def get_popular_articles(query, cur_user):
        offsets.append(query.offset)
        return pages[query.offset]

    monkeypatch.setattr(shared_utils, "get_popular_articles", get_popular_articles)

    result = shared_utils.get_popular_articles_by_tags(ArticleQueryDTO(tags=["aws"], limit=10))

    assert [item.id for item in result] == ["aws-1"]
    assert offsets == [None, "next-page"]


def test_popular_articles_by_tags_uses_last_match_as_pagination_cursor(monkeypatch):
    page = [
        article("global-1", ["databases"], 30),
        article("aws-1", ["aws"], 20),
        article("aws-2", ["aws"], 10),
        article("aws-3", ["aws"], 5, "end-of-query-page"),
    ]
    monkeypatch.setattr(shared_utils, "get_popular_articles", lambda query, cur_user: page)

    result = shared_utils.get_popular_articles_by_tags(ArticleQueryDTO(tags=["aws"], limit=2))

    assert [item.id for item in result] == ["aws-1", "aws-2"]
    assert shared_utils.decode_offset(result[-1].offset) == {
        "pk": "POST#aws-2",
        "sk": "META",
        "post_status_pk": "POST#published",
        "rating_sk": 10,
    }


def test_related_articles_only_requests_enough_candidates(monkeypatch):
    current = article("current", ["aws", "python"], 40)
    candidates = [
        current,
        article("related-1", ["aws"], 30),
        article("related-2", ["aws", "python"], 20),
    ]
    captured = {}

    def get_popular_articles_by_tags(query, or_mode=False):
        captured["limit"] = query.limit
        captured["tags"] = query.tags
        captured["or_mode"] = or_mode
        return candidates

    monkeypatch.setattr(web_utils, "get_popular_articles_by_tags", get_popular_articles_by_tags)

    result = web_utils.get_article_related_articles(current, limit=2)

    assert [item.id for item in result] == ["related-2", "related-1"]
    assert captured == {"limit": 3, "tags": ["aws", "python"], "or_mode": True}
