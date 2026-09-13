import os
import sys
from pathlib import Path
from types import SimpleNamespace

project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
sys.path.insert(0, str(project_root / "shared"))

previous_cwd = os.getcwd()
os.chdir(project_root / "shared")
import shared_utils
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
