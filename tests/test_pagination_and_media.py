"""Regressions for stale category cursors and paginated navigation rendering."""
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import FileSystemLoader

project_root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(project_root / "shared"))
from api_route_metadata import API_URL_ROUTES
from query_dtos import ArticleListingQueryDTO, ArticleQueryDTO, UserQueryDTO, TagQueryDTO
from shared_utils import encode_offset, get_jinja2_env
from web import Application, Request, RequestValidationError, parse_dto
from web_route_metadata import WEB_URL_ROUTES


PRODUCTION_CURSOR = "eyJwayI6ICJQT1NUI2VhMWMwZmE3LTA3NWYtNDVlMi1hYTQ2LTRkZjQ1NDBjODg4NiIsICJyYXRpbmdfc2siOiAxMTc4ODA1MzI2NDIzNiwgInBvc3Rfc3RhdHVzX3BrIjogIlBPU1QjcHVibGlzaGVkIiwgInNrIjogIk1FVEEifQ=="


def test_production_popular_cursor_is_rejected_for_latest_tag_listing():
    with pytest.raises(RequestValidationError, match="offset"):
        parse_dto(ArticleListingQueryDTO, {"tags": ["web-protocols"], "offset": PRODUCTION_CURSOR})
    query = parse_dto(ArticleListingQueryDTO, {"tags": ["web-protocols"], "type": "popular",
                                              "offset": PRODUCTION_CURSOR})
    assert query.offset == PRODUCTION_CURSOR


@pytest.mark.parametrize("sort_order,sort_key", [("latest", "created_at"), ("popular", "rating_sk")])
def test_global_listing_cursor_matches_sort_and_status(sort_order, sort_key):
    offset = encode_offset({"pk": "POST#example", "sk": "META", "post_status_pk": "POST#published",
                            sort_key: 1782360470433})
    assert parse_dto(ArticleListingQueryDTO, {"type": sort_order, "offset": offset}).offset == offset
    for params in ({"type": "popular" if sort_order == "latest" else "latest"}, {"status": "unpublished"}):
        with pytest.raises(RequestValidationError, match="offset"):
            parse_dto(ArticleListingQueryDTO, {"type": sort_order, "offset": offset, **params})


def test_latest_tag_cursor_matches_tag_combination():
    offset = encode_offset({"pk": "POST_TAG_COMBO#http#web-protocols", "sk": "POST#1782360470433#example"})
    assert parse_dto(ArticleListingQueryDTO, {"tags": ["web-protocols", "http"], "offset": offset}).offset == offset
    for params in ({"tags": ["http"]}, {"tags": ["http", "web-protocols"], "type": "popular"}, {}):
        with pytest.raises(RequestValidationError, match="offset"):
            parse_dto(ArticleListingQueryDTO, {"offset": offset, **params})


@pytest.mark.parametrize("offset", ["invalid", encode_offset(["unexpected"]), encode_offset({"pk": "POST#1"})])
def test_invalid_listing_cursor_is_a_request_validation_error(offset):
    with pytest.raises(RequestValidationError, match="offset"):
        parse_dto(ArticleListingQueryDTO, {"offset": offset})


def test_user_article_cursor_remains_accepted_by_profile_query():
    offset = encode_offset({"pk": "POST#example", "sk": "META", "post_user_status_pk": "POST#user#published",
                            "created_at": 1782360470433})
    assert parse_dto(ArticleQueryDTO, {"offset": offset}).offset == offset


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.setdefault(tag, []).append(dict(attrs))


def cursor(sort_key="created_at", category="cloud-infrastructure", status="published"):
    return encode_offset({"post_category_status_pk": f"POST#{category}#{status}", "sk": "META",
                          "pk": "POST#92ae4775-5c4e-451d-b713-b4fc3c0462b0", sort_key: 1782360470433})


@pytest.mark.parametrize("sort_order,sort_key", [("latest", "created_at"), ("popular", "rating_sk")])
def test_matching_category_cursor_is_accepted(sort_order, sort_key):
    query = parse_dto(ArticleQueryDTO, {"category": "cloud-infrastructure", "type": sort_order,
                                       "offset": cursor(sort_key)})
    assert query.offset == cursor(sort_key)


@pytest.mark.parametrize("offset", [cursor(), cursor("rating_sk", "other"),
                                     cursor("rating_sk", status="unpublished"),
                                     "invalid", encode_offset(["unexpected"]), encode_offset({"pk": "POST#1"})])
def test_stale_or_invalid_category_cursors_are_request_validation_errors(offset):
    with pytest.raises(RequestValidationError, match="offset"):
        parse_dto(ArticleQueryDTO, {"category": "cloud-infrastructure", "type": "popular", "offset": offset})


@pytest.fixture
def render():
    app = Application()
    for name, path in (WEB_URL_ROUTES | API_URL_ROUTES).items():
        app.add_url_route(path, name)
    request = Request({"type": "http", "method": "GET", "path": "/articles", "root_path": "",
                       "query_string": b"", "scheme": "https", "server": ("example.com", 443),
                       "headers": [], "app": app})
    env = get_jinja2_env()
    env.loader = FileSystemLoader([str(project_root / "web-lambda/templates"),
                                  str(project_root / "shared/templates")])

    def render_template(template, metadata_only=False, **data):
        page = env.from_string('{% extends "' + template + '" %}{% block content %}{% endblock %}') \
            if metadata_only else env.get_template(template)
        return page.render(request=request, cur_user=None, **data)
    return render_template


@pytest.mark.parametrize("template,data", [
    ("articles.html", {"article_query": ArticleQueryDTO(category="cloud-infrastructure", offset=cursor()),
                       "articles": [], "tag": None, "category": None,
                       "article_query_tag_names": [], "article_query_tag_items": []}),
    ("users.html", {"user_query": UserQueryDTO(offset="previous-page"), "users": []}),
    ("tags.html", {"tags_query": TagQueryDTO(offset="previous-page"), "tags": []}),
])
def test_sort_links_restart_pagination(render, template, data):
    links = Elements(render(template, **data)).tags["a"]
    sort_links = [link for link in links if "btn" in link.get("class", "")
                  and ("bi-clock" in link["class"] or "bi-star" in link["class"] or "bi-heart" in link["class"])]
    assert len(sort_links) == 2
    for link in sort_links:
        params = parse_qs(urlsplit(link["href"]).query)
        assert "offset" not in params
        if template == "articles.html":
            assert params["category"] == ["cloud-infrastructure"]


@pytest.mark.parametrize("template,query_name,query_type,extra", [
    ("articles.html", "article_query", ArticleQueryDTO,
     {"articles": [SimpleNamespace(id="example", title="Example", slug="example", user_slug="author",
                                   tags=[], status="published", created_at=1782360470433,
                                   offset=None, rating=0, comments_count=0)],
      "tag": None, "category": None, "article_query_tag_names": [], "article_query_tag_items": []}),
    ("users.html", "user_query", UserQueryDTO, {"users": []}),
    ("tags.html", "tags_query", TagQueryDTO, {"tags": []}),
    ("user.html", "article_query", ArticleQueryDTO,
     {"user": SimpleNamespace(id="author", username="author", name="Author", status="active",
                              headline="", about="", avatar_filename=None, website=None, github_username=None)}),
])
def test_cursor_listing_pages_are_not_indexable(render, template, query_name, query_type, extra):
    # Render the real metadata without requiring full models for the page body.
    for offset, expected in ((None, "index, follow"), ("continuation", "noindex, follow")):
        html = render(template, metadata_only=True, **{query_name: query_type(offset=offset), **extra})
        robots = [meta["content"] for meta in Elements(html).tags["meta"] if meta.get("name") == "robots"]
        assert robots == [expected]


def test_category_covers_have_safe_standalone_and_listing_loading(render):
    categories = [SimpleNamespace(name=f"Category {i}", slug=f"category-{i}", description="Description",
                                  published_articles_count=0, image_filename=f"category-{i}_1200x800.jpg")
                  for i in range(3)]
    images = Elements(render("categories.html", categories=categories)).tags["img"]
    assert [image["loading"] for image in images] == ["eager", "eager", "lazy"]
    assert all(image["decoding"] == "async" for image in images)
    standalone = Elements(render("fragments/category.html", category=categories[0])).tags["img"]
    assert standalone[0]["loading"] == "lazy"


def test_avif_image_uses_responsive_webp_variants(render):
    elements = Elements(render("fragments/image.html", filename="example_1280x800.avif", alt="Example"))
    source = elements.tags["source"][0]
    assert source["type"] == "image/webp"
    assert "example_640x400.webp" in source["srcset"]
    assert elements.tags["img"][0]["src"].endswith("example_1280x800.avif")
