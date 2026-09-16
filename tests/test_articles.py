import asyncio
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


def test_related_articles_query_each_tag_and_batch_load_candidates(monkeypatch):
    current = article("current", ["aws", "python"], 40)
    candidates = [
        article("related-1", ["aws"], 30),
        article("related-2", ["aws", "python"], 20),
    ]
    captured_queries = []
    captured_batch_ids = []

    def get_article_ids_by_tag(tag, limit):
        captured_queries.append((tag, limit))
        return {
            "aws": ["current", "related-1", "related-2"],
            "python": ["current", "related-2"],
        }[tag]

    def get_articles_by_ids(article_ids):
        captured_batch_ids.extend(article_ids)
        return candidates

    monkeypatch.setattr(web_utils, "_get_article_ids_by_tag", get_article_ids_by_tag)
    monkeypatch.setattr(web_utils, "_get_articles_by_ids", get_articles_by_ids)

    result = asyncio.run(web_utils.get_article_related_articles(current, limit=2))

    assert [item.id for item in result] == ["related-2", "related-1"]
    assert sorted(captured_queries) == [("aws", 3), ("python", 3)]
    assert captured_batch_ids == ["related-1", "related-2"]


def test_related_article_batch_read_retries_unprocessed_keys(monkeypatch):
    unprocessed_key = {"pk": "POST#related-2", "sk": "META"}

    class Client:
        def __init__(self):
            self.requests = []

        def batch_get_item(self, RequestItems):
            self.requests.append(RequestItems)
            if len(self.requests) == 1:
                return {
                    "Responses": {"articles": [{"id": "related-1"}]},
                    "UnprocessedKeys": {"articles": {"Keys": [unprocessed_key]}},
                }
            return {
                "Responses": {"articles": [{"id": "related-2"}]},
                "UnprocessedKeys": {},
            }

    client = Client()
    table = SimpleNamespace(name="articles", meta=SimpleNamespace(client=client))
    monkeypatch.setattr(web_utils, "get_dynamodb_table", lambda: table)
    monkeypatch.setattr(web_utils, "article_from_dynamodb", lambda item: item["id"])

    result = web_utils._get_articles_by_ids(["related-1", "related-2"])

    assert result == ["related-1", "related-2"]
    assert client.requests[1] == {"articles": {"Keys": [unprocessed_key]}}
