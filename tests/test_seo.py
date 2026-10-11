"""SEO regressions that do not require production services."""
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

import pytest

from jinja2 import Environment, DictLoader

project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parents[1]))
for directory in ("shared", "api-lambda", "web-lambda"):
    sys.path.insert(0, str(project_root / directory))

import api_utils
import shared_utils
import web_utils
from query_dtos import ArticleQueryDTO


def test_sitemap_escapes_urls_paginates_tags_and_uses_real_dates(monkeypatch):
    saved = []
    queries = []
    submissions = []
    tag = SimpleNamespace(slug="cache", articles_count=1, offset="next-tags")
    last_tag = SimpleNamespace(slug="queues", articles_count=1, offset=None)

    def tags(query):
        queries.append(query.offset)
        return [tag] if query.offset is None else [last_tag]

    monkeypatch.setattr(api_utils, "verify_authorization", lambda *a: None)
    monkeypatch.setattr(api_utils, "get_url", lambda req, name, absolute: f"https://example.com/{name}?a=1&b=2")
    monkeypatch.setattr(api_utils, "get_articles_url", lambda req, **kw:
                        f"https://example.com/{kw['type']}/{kw.get('category') or '/'.join(kw['tags'])}/articles")
    monkeypatch.setattr(api_utils, "get_users_url", lambda req, **kw: "https://example.com/users")
    monkeypatch.setattr(api_utils, "get_tags", tags)
    monkeypatch.setattr(api_utils, "get_categories", lambda: [
        SimpleNamespace(slug="architecture", published_articles_count=1),
        SimpleNamespace(slug="empty", published_articles_count=0)])
    monkeypatch.setattr(api_utils, "get_latest_articles", lambda query: [
        SimpleNamespace(updated_at=1_700_000_000_000, published_at=1_650_000_000_000, created_at=1_600_000_000_000, offset=None)])
    monkeypatch.setattr(api_utils, "get_article_url", lambda *a, **kw: "https://example.com/@author/article")
    monkeypatch.setattr(api_utils, "get_latest_users", lambda query: [])
    monkeypatch.setattr(api_utils, "save_public_file", lambda dto, **kw: saved.append(dto.content) or "sitemap.xml")
    monkeypatch.setattr(api_utils, "get_static_url", lambda *a, **kw: "https://static.example.com/sitemap.xml")
    monkeypatch.setattr(api_utils, "is_prod", lambda: True)
    monkeypatch.setattr(api_utils, "safe_execute", lambda *a: submissions.append(a))

    count, _ = api_utils.generate_sitemap(None, None)

    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries = fromstring(saved[0]).findall("s:url", ns)
    urls = [e.findtext("s:loc", namespaces=ns) for e in entries]
    assert count == len(urls) == len(set(urls))
    assert queries == [None, "next-tags"]
    assert "https://example.com/policy?a=1&b=2" in urls
    assert "https://example.com/latest/queues/articles" in urls
    article = entries[urls.index("https://example.com/@author/article")]
    assert article.findtext("s:lastmod", namespaces=ns) == "2023-11-14"
    assert entries[0].find("s:lastmod", ns) is None
    assert "https://example.com/latest/architecture/articles" in urls
    assert not any("/empty/" in url for url in urls)
    assert submissions[0][2] == ["/sitemap*"]
    assert submissions[1][2] == "https://static.example.com/sitemap.xml"


def test_tag_archive_keeps_query_order_and_cursor_after_batch_read(monkeypatch):
    cursor = {"pk": "POST_TAG_COMBO#cache", "sk": "last"}
    client = SimpleNamespace(batch_get_item=lambda **kw: {
        "Responses": {"articles": [{"id": "older"}, {"id": "newer"}]}})
    table = SimpleNamespace(name="articles", meta=SimpleNamespace(client=client), query=lambda **kw: {
        "Items": [{"post_id": "newer"}, {"post_id": "older"}], "LastEvaluatedKey": cursor})
    monkeypatch.setattr(shared_utils, "get_dynamodb_table", lambda: table)
    monkeypatch.setattr(shared_utils, "article_from_dynamodb", lambda item: SimpleNamespace(id=item["id"], offset=None))

    articles = shared_utils.get_latest_articles_by_tags(ArticleQueryDTO(tags=["cache"]))

    assert [article.id for article in articles] == ["newer", "older"]
    assert shared_utils.decode_offset(articles[-1].offset) == cursor


def test_concurrent_web_renders_do_not_share_request_context(monkeypatch):
    env = Environment(loader=DictLoader({"page.html": "{{ request.path }}"}))
    monkeypatch.setattr(shared_utils, "jinja2_env", lambda: env)

    async def render(path):
        token = web_utils.template_request.set(SimpleNamespace(path=path))
        try:
            await asyncio.sleep(0)
            return web_utils.get_web_html_content("page.html")
        finally:
            web_utils.template_request.reset(token)

    async def run():
        return await asyncio.gather(render("/articles"), render("/users"))

    assert asyncio.run(run()) == ["/articles", "/users"]
    assert "request" not in env.globals


def test_static_build_excludes_locally_generated_sitemap(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "generate_site_build.py"
    shutil.copyfile(project_root / "scripts/generate_site_build.py", script)
    static = tmp_path / "static"
    static.mkdir()
    (static / "sitemap.xml").write_text("http://web-lambda:5000/test-fixture")
    (static / "sitemap-1.xml").write_text("local sitemap shard")
    (static / "styles.css").write_text("body {}")

    subprocess.run([sys.executable, str(script)], check=True,
                   env={**os.environ, "STATIC_FILES_DIR": "static"}, capture_output=True)

    assert not list((tmp_path / ".site-build").glob("sitemap*.xml"))
    assert (tmp_path / ".site-build/styles.css").read_text() == "body {}"


@pytest.mark.parametrize("by_bytes", [False, True])
def test_sitemap_splits_at_url_and_byte_limits(monkeypatch, by_bytes):
    files = {}
    monkeypatch.setattr(api_utils, "SITEMAP_MAX_URLS", 50_000 if by_bytes else 2)
    monkeypatch.setattr(api_utils, "SITEMAP_MAX_BYTES", 900 if by_bytes else 50 * 1024 * 1024)
    monkeypatch.setattr(api_utils, "save_public_file", lambda dto, **kw: files.setdefault(kw["filename"], dto.content) and kw["filename"])
    monkeypatch.setattr(api_utils, "get_static_url", lambda req, filename, **kw: "https://static.example.com/" + filename)
    urls = [f'https://example.com/{i}/é"&<>' + "a" * 350 for i in range(5)]
    count, root_url = api_utils._save_sitemap(iter([(url, None) for url in urls] + [(urls[0], None)]), None)
    ns = {"s": api_utils.SITEMAP_NAMESPACE}
    index = fromstring(files["sitemap.xml"])
    assert index.tag.endswith("sitemapindex")
    assert root_url == "https://static.example.com/sitemap.xml"
    found = []
    for node in index.findall("s:sitemap", ns):
        filename = node.findtext("s:loc", namespaces=ns).rsplit("/", 1)[1]
        content = files[filename]
        entries = fromstring(content).findall("s:url", ns)
        assert len(entries) <= api_utils.SITEMAP_MAX_URLS
        assert len(content) <= api_utils.SITEMAP_MAX_BYTES
        found.extend(entry.findtext("s:loc", namespaces=ns) for entry in entries)
    assert count == 5
    assert found == urls
    assert list(files)[-1] == "sitemap.xml"


def test_oversized_sitemap_entry_is_not_uploaded(monkeypatch):
    monkeypatch.setattr(api_utils, "SITEMAP_MAX_BYTES", 200)
    uploads = []
    monkeypatch.setattr(api_utils, "save_public_file", lambda *a, **kw: uploads.append(kw))
    with pytest.raises(ValueError, match="entry exceeds"):
        api_utils._save_sitemap([("https://example.com/" + "a" * 300, None)], None)
    assert not uploads


@pytest.mark.parametrize("type_", ["latest", "popular"])
@pytest.mark.parametrize("empty_final_page", [False, True])
def test_category_tag_filter_fills_page_and_keeps_scanned_cursor(monkeypatch, type_, empty_final_page):
    query = ArticleQueryDTO(category="architecture", tags=["queues"], type=type_, limit=2)
    calls = []
    pages = {
        None: [SimpleNamespace(id="skip", tags=["cache"], offset=None),
               SimpleNamespace(id="first", tags=["queues"], offset="page-two")],
        "page-two": [SimpleNamespace(id="skip-again", tags=[], offset="page-three")],
        "page-three": [] if empty_final_page else [SimpleNamespace(id="second", tags=["queues"], offset="page-four")],
    }
    def fetch(page_query, user):
        calls.append((page_query.limit, page_query.offset))
        return pages[page_query.offset]
    monkeypatch.setattr(shared_utils, "get_latest_articles", fetch)
    monkeypatch.setattr(shared_utils, "get_popular_articles", fetch)
    articles = shared_utils.get_articles(query)
    assert [article.id for article in articles] == (["first"] if empty_final_page else ["first", "second"])
    assert articles[-1].offset == (None if empty_final_page else "page-four")
    assert calls == [(2, None), (1, "page-two"), (1, "page-three")]
    assert query.limit == 2 and query.offset is None


def test_tag_filters_share_a_sorted_unique_url(monkeypatch):
    monkeypatch.setattr(shared_utils, "get_url", lambda req, route, **kw: (route, kw))
    query = ArticleQueryDTO(tags=["queues", "cache", "queues", ""])
    assert query.tags == ["cache", "queues"]
    assert shared_utils.get_articles_url(None, tags=["queues", "cache", "queues"]) == shared_utils.get_articles_url(None, query=query)


def test_sitemap_consumes_article_and_profile_pages_with_real_fallback_dates(monkeypatch):
    saved = {}
    article_offsets, user_offsets = [], []
    def articles(query):
        article_offsets.append(query.offset)
        return [SimpleNamespace(id="first" if query.offset is None else "second",
                                updated_at=None, published_at=1_700_000_000_000,
                                created_at=1_600_000_000_000,
                                offset="more-articles" if query.offset is None else None)]
    def users(query):
        user_offsets.append(query.offset)
        return [SimpleNamespace(id="first" if query.offset is None else "second",
                                updated_at=None, created_at=1_600_000_000_000,
                                offset="more-users" if query.offset is None else None)]
    monkeypatch.setattr(api_utils, "verify_authorization", lambda *a: None)
    monkeypatch.setattr(api_utils, "get_url", lambda req, route, absolute: f"https://example.com/{route}")
    monkeypatch.setattr(api_utils, "get_articles_url", lambda req, **kw: f"https://example.com/{kw['type']}/articles")
    monkeypatch.setattr(api_utils, "get_users_url", lambda req, **kw: f"https://example.com/{kw['type']}/users")
    monkeypatch.setattr(api_utils, "get_article_url", lambda req, article, **kw: f"https://example.com/articles/{article.id}")
    monkeypatch.setattr(api_utils, "get_user_url", lambda req, user, **kw: f"https://example.com/users/{user.id}")
    monkeypatch.setattr(api_utils, "get_tags", lambda query: [])
    monkeypatch.setattr(api_utils, "get_categories", lambda: [])
    monkeypatch.setattr(api_utils, "get_latest_articles", articles)
    monkeypatch.setattr(api_utils, "get_latest_users", users)
    monkeypatch.setattr(api_utils, "save_public_file", lambda dto, **kw: saved.setdefault(kw["filename"], dto.content) and kw["filename"])
    monkeypatch.setattr(api_utils, "get_static_url", lambda req, filename, **kw: "https://static.example.com/" + filename)
    monkeypatch.setattr(api_utils, "is_prod", lambda: False)
    monkeypatch.setattr(api_utils, "safe_execute", lambda *a: None)
    api_utils.generate_sitemap(None, None)
    ns = {"s": api_utils.SITEMAP_NAMESPACE}
    dates = {entry.findtext("s:loc", namespaces=ns): entry.findtext("s:lastmod", namespaces=ns)
             for entry in fromstring(saved["sitemap.xml"]).findall("s:url", ns)}
    assert article_offsets == [None, "more-articles"]
    assert user_offsets == [None, "more-users"]
    assert dates["https://example.com/articles/second"] == "2023-11-14"
    assert dates["https://example.com/users/second"] == "2020-09-13"


@pytest.mark.parametrize("index_name", [None, "POSTS_BY_CATEGORY_STATUS_RATING"])
def test_query_validation_errors_are_not_reported_as_empty_results(monkeypatch, caplog, index_name):
    from botocore.exceptions import ClientError
    error = ClientError({"Error": {
        "Code": "ValidationException", "Message": "The provided starting key is invalid",
    }}, "Query")
    def query(**kwargs):
        raise error
    monkeypatch.setattr(shared_utils, "get_dynamodb_table", lambda: SimpleNamespace(query=query))
    with pytest.raises(ClientError) as caught:
        shared_utils.query_dynamodb_table(index_name=index_name)
    assert caught.value is error
    assert "The provided starting key is invalid" in caplog.text
    assert (index_name or "base table") in caplog.text


def test_tag_prefix_query_keeps_alphabetical_order_and_continuation(monkeypatch):
    from query_dtos import TagQueryDTO
    cursor = {"pk": "POST_TAG#queues", "sk": "META"}
    calls = []
    def query(**kwargs):
        calls.append(kwargs)
        return {"Items": [{"tag_name_sk": "queues", "rating_sk": 1}], "LastEvaluatedKey": cursor}
    monkeypatch.setattr(shared_utils, "query_dynamodb_table", query)
    tags = shared_utils.get_tags_by_prefix(TagQueryDTO(prefix="que", limit=1, offset=shared_utils.encode_offset(cursor)))
    assert calls[0]["scan_index_forward"] is True
    assert calls[0]["exclusive_start_key"] == cursor
    assert shared_utils.decode_offset(tags[-1].offset) == cursor
