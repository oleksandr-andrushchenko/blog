import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
sys.path.insert(0, str(project_root / "shared"))
sys.path.insert(0, str(project_root / "web-lambda"))

previous_cwd = os.getcwd()
os.chdir(project_root / "shared")
import shared_utils
import related_articles
import web_utils
from article_dtos import ArticleDTO, UpdateArticleDTO
from query_dtos import ArticleCommentQueryDTO, ArticleQueryDTO
from validation import normalize_slug
os.chdir(previous_cwd)


def test_article_category_uses_valid_slug():
    content = "x" * 5_000
    article_dto = ArticleDTO(
        title="Example",
        content=content,
        tags=["example"],
        category="distributed-systems",
    )
    assert article_dto.category == "distributed-systems"

    try:
        UpdateArticleDTO(category="Invalid category")
    except ValueError as exc:
        assert "unsupported article category" in str(exc)
    else:
        raise AssertionError("invalid categories must be rejected")


@pytest.mark.parametrize("title", ["...", "___--", "😀😀", "Bad\x00title", "Bad\ud800title", "Bad\ufffdtitle"])
def test_article_titles_reject_empty_slugs_and_invalid_text(title):
    with pytest.raises(ValueError):
        ArticleDTO(title=title, content="x" * 5000, tags=["valid"], category="distributed-systems")
    with pytest.raises(ValueError):
        UpdateArticleDTO(title=title)


def test_new_slugs_normalize_separators_and_preserve_unicode_and_legacy_tag_keys():
    dto = ArticleDTO(title="  Café___-- Привіт --  ", content="x" * 5000,
                     tags=["  Cloud___--Design  ", "Café"], category="distributed-systems")
    assert normalize_slug(dto.title) == "café-привіт"
    assert dto.tags == ["cloud-design", "café"]
    assert shared_utils.sanitize_tags(["legacy__tag--", "Привіт"]) == ["legacy__tag--", "привіт"]
    assert shared_utils.tag_subscription_key(["legacy__tag--"]) == "legacy__tag--"


@pytest.mark.parametrize("tag", ["bad\x00tag", "bad\ufffdtag", "bad\ud800tag"])
def test_new_article_tags_reject_invalid_text(tag):
    with pytest.raises(ValueError):
        UpdateArticleDTO(tags=[tag])


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


def test_homepage_comments_skip_nonpublic_articles_and_fill_sparse_pages(monkeypatch):
    public = SimpleNamespace(id="public", status="published", title="Current title")
    articles = {
        "public": public,
        "draft": SimpleNamespace(status="unpublished"),
        "rejected": SimpleNamespace(status="rejected"),
        "deleted": None,
    }
    pages = {
        None: {"Items": [{"post_id": key} for key in ("draft", "rejected", "deleted", "draft")],
               "LastEvaluatedKey": {"pk": "next"}},
        "next": {"Items": [], "LastEvaluatedKey": {"pk": "last"}},
        "last": {"Items": [{"post_id": "public", "id": str(i)} for i in range(3)]},
    }
    queries = []
    lookups = []

    def query(**kwargs):
        offset = kwargs["exclusive_start_key"]
        queries.append(offset)
        return pages[offset["pk"] if offset else None]

    def find(article_id):
        lookups.append(article_id)
        return articles[article_id]

    monkeypatch.setattr(web_utils, "query_dynamodb_table", query)
    monkeypatch.setattr(web_utils, "find_article", find)
    monkeypatch.setattr(web_utils, "article_comment_from_dynamodb", lambda item: item["id"])
    result = web_utils.get_latest_article_comments(ArticleCommentQueryDTO(limit=2))

    assert result == [{"comment": "0", "article": public}, {"comment": "1", "article": public}]
    assert queries == [None, {"pk": "next"}, {"pk": "last"}]
    assert lookups == ["draft", "rejected", "deleted", "public"]


def test_related_articles_query_each_tag_and_batch_load_candidates(monkeypatch):
    current = article("current", ["aws", "python"], 40)
    candidates = [
        article("related-1", ["aws"], 30),
        article("related-2", ["aws", "python"], 20),
    ]
    captured_queries = []
    captured_batch_ids = []

    def get_article_ids_by_tag(tag, limit, offset=None):
        captured_queries.append((tag, limit))
        return {
            "aws": ["current", "related-1", "related-2"],
            "python": ["current", "related-2"],
        }[tag], None

    def get_articles_by_ids(article_ids):
        captured_batch_ids.extend(article_ids)
        return [candidate for candidate in candidates if candidate.id in article_ids]

    monkeypatch.setattr(related_articles, "_get_article_ids_by_tag", get_article_ids_by_tag)
    monkeypatch.setattr(related_articles, "_get_articles_by_ids", get_articles_by_ids)

    result = asyncio.run(related_articles.get_related_articles(current, limit=2))

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
    monkeypatch.setattr(related_articles, "get_dynamodb_table", lambda: table)
    monkeypatch.setattr(related_articles, "article_from_dynamodb", lambda item: item["id"])

    result = related_articles._get_articles_by_ids(["related-1", "related-2"])

    assert result == ["related-1", "related-2"]
    assert client.requests[1] == {"articles": {"Keys": [unprocessed_key]}}


def test_related_batch_read_does_not_advance_past_unprocessed_keys(monkeypatch):
    key = {"pk": "POST#pending", "sk": "META"}
    client = SimpleNamespace(batch_get_item=lambda **kwargs: {
        "Responses": {}, "UnprocessedKeys": {"articles": {"Keys": [key]}},
    })
    monkeypatch.setattr(related_articles, "get_dynamodb_table", lambda: SimpleNamespace(
        name="articles", meta=SimpleNamespace(client=client)))
    monkeypatch.setattr(related_articles.time, "sleep", lambda delay: None)
    with pytest.raises(RuntimeError, match="unprocessed"):
        related_articles._get_articles_by_ids(["pending"])


def test_related_pagination_keeps_candidates_and_deduplicates_later_tag_pages(monkeypatch):
    current = article('source', ['a', 'b'], 0)
    items = {
        'source': current, 'a1': article('a1', ['a'], 10), 'a2': article('a2', ['a'], 20),
        'shared': article('shared', ['a', 'b'], 5), 'b1': article('b1', ['b'], 30),
        'b2': article('b2', ['b'], 40), 'draft': article('draft', ['a'], 100),
        'unrelated': article('unrelated', ['other'], 200),
    }
    items['draft'].status = 'unpublished'
    streams = {'a': ['source', 'a1', 'draft', 'unrelated', 'a2', 'shared'],
               'b': ['shared', 'b1', 'b2', 'a1']}
    queries = []

    def read_tag(tag, limit, offset=None):
        queries.append(tag)
        start = int(offset['sk'].split('#')[1]) if offset else 0
        end = min(start + limit, len(streams[tag]))
        next_key = {'pk': f'POST_TAG_COMBO#{tag}', 'sk': f'POST#{end}#cursor'} if end < len(streams[tag]) else None
        return streams[tag][start:end], next_key

    monkeypatch.setattr(related_articles, '_get_article_ids_by_tag', read_tag)
    monkeypatch.setattr(related_articles, '_get_articles_by_ids', lambda ids: [items[key] for key in ids])
    seen, offset = [], None
    for _ in range(5):
        page = asyncio.run(related_articles.get_related_articles(current, limit=2, offset=offset))
        assert page
        seen.extend(item.id for item in page)
        offset = page[-1].offset
        if offset is None:
            break
        state = shared_utils.decode_offset(offset)
        assert set(state) == {'article_id', 'tags', 'positions', 'pending'}
        assert len(state['pending']) <= 6
    assert offset is None
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == {'a1', 'a2', 'shared', 'b1', 'b2'}
    assert sorted(queries) == ['a', 'a', 'b', 'b']


@pytest.mark.parametrize('state', [
    [], {'article_id': 'other', 'tags': ['a'], 'positions': {'a': None}, 'pending': []},
    {'article_id': 'source', 'tags': ['a'], 'positions': {'a': {'pk': 'POST_TAG_COMBO#other', 'sk': 'POST#1#p'}},
     'pending': []},
    {'article_id': 'source', 'tags': ['a'], 'positions': {'a': None}, 'pending': [['bad']]},
])
def test_related_cursors_reject_other_scopes_and_invalid_state(state):
    import base64
    import json
    offset = base64.urlsafe_b64encode(json.dumps(state).encode()).decode()
    with pytest.raises(ValueError, match='cursor'):
        asyncio.run(related_articles.get_related_articles(article('source', ['a'], 0), offset=offset))
