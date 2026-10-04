#!/usr/bin/env python3

import json
import os
import time
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote

import pytest
from pyquery import PyQuery as pq

from test_utils import (
    WEB_TEST_BASE_URL,
    recreate_dynamodb_table,
    get_guest_client,
    get_logged_in_client,
    get,
    post,
    patch,
    delete,
    regular_user,
    regular_2_user,
    root_user,
    set_dynamodb_user_permissions,
    get_dynamodb_user,
    get_dynamodb_user_by_email,
    get_dynamodb_article,
    dynamodb_table,
)

pytestmark = pytest.mark.functional


def get_client(request, user):
    client_name = f"{user}_user_client"
    client = request.getfixturevalue(client_name)
    return client


def get_user(client, user_alias) -> pq:
    user = get_dynamodb_user(user_ids[user_alias])
    resp = get(client, f"/users/{user['id']}")
    assert resp.status_code == 200
    doc = pq(resp.text)
    assert user["name"] in doc("head title").text()
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == "Person"
    assert schema["url"].startswith("http")
    assert schema["breadcrumb"]["itemListElement"][-1]["name"] == user["name"]
    assert all(value is not None for value in schema.values())
    assert doc('meta[property="og:type"]').attr("content") == "profile"
    assert doc('meta[name="description"]').attr("content").startswith(user["name"])
    main_el = doc("main")
    assert user["name"] in main_el("h1").text()
    return doc


def get_logged_in_user_id(user_data: dict) -> str:
    return get_dynamodb_user_by_email(user_data["email"])["id"]


def get_index(client):
    resp = get(client, "/")
    assert resp.status_code == 200
    doc = pq(resp.text)
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == "WebSite"
    assert schema["url"].startswith("http")
    assert doc('meta[name="robots"]').attr("content") == "index, follow"
    assert doc('link[rel="canonical"]').attr("href").startswith("http")
    return doc


def get_users(client):
    resp = get(client, "/users")
    assert resp.status_code == 200
    doc = pq(resp.text)
    assert "users" in doc("head title").text().lower()
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == "CollectionPage"
    assert schema["mainEntity"]["@type"] == "ItemList"
    assert schema["breadcrumb"]["itemListElement"][-1]["name"] == "Users"
    assert doc('link[rel="canonical"]').attr("href").endswith("/users")
    main_el = doc("main")
    assert "users" in main_el("h1").text().lower()
    return doc


def get_user_by_id(client, user):
    resp = get(client, f"/users/{user['id']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_user_by_slug(client, user):
    resp = get(client, f"/@{user['username']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_articles(client):
    resp = get(client, "/articles")
    assert resp.status_code == 200
    doc = pq(resp.text)
    assert "articles" in doc("head title").text().lower()
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == "CollectionPage"
    assert schema["mainEntity"]["@type"] == "ItemList"
    assert schema["breadcrumb"]["itemListElement"][-1]["name"] == "Articles"
    assert doc('link[rel="canonical"]').attr("href").endswith("/articles")
    main_el = doc("main")
    assert "articles" in main_el("h1").text().lower()
    return doc


def get_article_by_id(client, article):
    resp = get(client, f"/articles/{article['id']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_article_by_slug(client, article):
    resp = get(client, f"/@{article['user_slug']}/{article['slug']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_contacts(client):
    resp = get(client, "/contacts")
    assert resp.status_code == 200
    doc = pq(resp.text)
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == "ContactPage"
    assert schema["breadcrumb"]["itemListElement"][-1]["name"] == "Contacts"
    assert doc('meta[name="description"]').attr("content").startswith("Contact SysDesPro")
    return doc


def get_user_href(user: dict) -> str:
    if username := user.get("username"):
        return f"/@{username}"
    return f"/users/{user['id']}"


def get_article_href(article: dict, user: dict | None = None) -> str:
    if username := user.get("username"):
        return f"/@{username}"
    return f"/users/{user['id']}"


def check_header(doc, user_alias: str | None):
    header_el = doc("header")
    assert header_el('a[href$="/"]')
    assert header_el('a[href$="/articles"]')
    assert header_el('a[href$="/users"]')
    assert header_el('a[href$="/contacts"]')
    if user_alias:
        assert header_el('a[href$="/articles/new"]')
        assert header_el('a[href$="/logout"][rel~="nofollow"]')
        user = get_dynamodb_user(user_ids[user_alias])
        assert header_el('a[href$="' + get_user_href(user) + '"]')
    else:
        assert header_el('a[href$="/login"][rel~="nofollow"]')
        assert not header_el('a[href*="/login"]:not([rel~="nofollow"])')
        # todo: "*=" - contains
        user_view_el = header_el('a[href="/users/*"]')
        assert not user_view_el


def check_auth_links_are_nofollow(doc):
    auth_links = doc('a[href*="/login"], a[href*="/logout"]')
    assert auth_links
    for link in auth_links.items():
        assert "nofollow" in (link.attr("rel") or "").split()


def check_user_impressions(doc, followers_count: int, following_count: int, follow_control: bool, block_control: bool):
    main_el = doc("main")
    user_impressions_el = main_el(".user-impressions")
    assert user_impressions_el
    assert int(user_impressions_el(".followers-count").text()) == followers_count
    assert int(user_impressions_el(".following-count").text()) == following_count
    has_user_follow = user_impressions_el(".btn-user-follow")
    if follow_control:
        assert has_user_follow
    else:
        assert not has_user_follow
    has_user_block = user_impressions_el(".btn-user-block")
    if block_control:
        assert has_user_block
    else:
        assert not has_user_block


def check_user_edit(doc, user_alias: str | None):
    main_el = doc("main")
    if user_alias:
        user = get_dynamodb_user(user_ids[user_alias])
        user_edit_el = main_el('a[href$="/users/' + user['id'] + '/edit"]')
        assert user_edit_el
    else:
        # todo:
        user_edit_el = main_el('a[href="/users/*/edit"]')
        assert not user_edit_el


def check_user_status(doc, activate_control: bool, ban_control: bool):
    main_el = doc("main")
    activate_el = main_el(".btn-user-activate")
    if activate_control:
        assert activate_el
    else:
        assert not activate_el
    ban_el = main_el(".btn-user-ban")
    if ban_control:
        assert ban_el
    else:
        assert not ban_el


def check_user(doc, followers_count: int, following_count: int, follow_control: bool, block_control: bool,
               user_alias: str | None, activate_control: bool, ban_control: bool):
    check_user_impressions(doc, followers_count=followers_count, following_count=following_count,
                           follow_control=follow_control, block_control=block_control)
    check_user_edit(doc, user_alias=user_alias)
    check_user_status(doc, activate_control=activate_control, ban_control=ban_control)


def check_articles(doc, articles_count: int, unpublished_control: bool, rejected_control: bool, tags_control: bool,
                   popular_control: bool, article_aliases: list[str], css_id="articles"):
    main_el = doc("main")
    articles_el = main_el("#" + css_id)
    if articles_count:
        assert len(articles_el(".article")) == articles_count
        for article_alias in article_aliases:
            article = get_dynamodb_article(article_ids[article_alias])
            user = get_dynamodb_user(article["user_id"])
            article_el = main_el('a[href$="' + get_article_href(article, user) + '"]')
            assert article_el
            assert article["title"] in article_el.text()
    else:
        assert not articles_el
    form_el = main_el("form")
    status_controls_el = form_el if form_el else main_el
    unpublished_el = status_controls_el('a[href*="status=unpublished"]')
    if unpublished_control:
        assert unpublished_el
    else:
        assert not unpublished_el
    rejected_el = status_controls_el('a[href*="status=rejected"]')
    if rejected_control:
        assert rejected_el
    else:
        assert not rejected_el
    tags_el = form_el('#tags-input')
    if tags_control:
        assert tags_el
    else:
        assert not tags_el
    popular_el = form_el('a[href*="popular"].bi-star')
    if popular_control:
        assert popular_el
    else:
        assert not popular_el


def check_users(doc, users_count: int, banned_control: bool, popular_control: bool, user_aliases: list[str],
                css_id="users"):
    main_el = doc("main")
    users_el = main_el("#" + css_id)
    if users_count:
        assert len(users_el(".user")) == users_count
        for user_alias in user_aliases:
            user = get_dynamodb_user(user_ids[user_alias])
            user_el = main_el('a[href$="' + get_user_href(user) + '"]')
            assert user_el
            assert user["name"] in user_el.text()
    else:
        assert not users_el
    form_el = main_el("form")
    banned_el = form_el('a[href*="status=banned"]')
    if banned_control:
        assert banned_el
    else:
        assert not banned_el
    popular_el = form_el('a[href*="popular"].bi-heart')
    if popular_control:
        assert popular_el
    else:
        assert not popular_el


def check_latest_article_comments(doc, comments_count: int, comment_texts: list[str]):
    main_el = doc("main")
    comments_el = main_el("#latest-article-comments")
    if comments_count:
        assert len(comments_el(".article-comment")) == comments_count
        rendered_text = comments_el.text()
        for comment_text in comment_texts:
            assert comment_text in rendered_text
    else:
        assert not comments_el


def check_index(doc):
    check_articles(doc, articles_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                   popular_control=False, article_aliases=list(article_ids.keys()), css_id="articles")
    check_articles(doc, articles_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                   popular_control=False, article_aliases=list(article_ids.keys()), css_id="popular-articles")
    check_latest_article_comments(doc, comments_count=0, comment_texts=[])
    check_users(doc, users_count=0, banned_control=False, popular_control=False, user_aliases=[], css_id="users")
    check_users(doc, users_count=3, banned_control=False, popular_control=False, user_aliases=list(user_ids.keys()),
                css_id="popular-users")


@pytest.fixture(scope="session", autouse=True)
def setup_dynamodb():
    recreate_dynamodb_table()
    for slug, name, description in [
        ("caching", "Caching", "Caching strategies, invalidation, and data access."),
        ("distributed-systems", "Distributed Systems", "Coordination, consistency, and distributed failure handling."),
        ("other", "Other", "System design topics that do not fit another category."),
    ]:
        dynamodb_table.put_item(Item={
            "pk": "CATEGORY",
            "sk": slug,
            "category_slug": slug,
            "name": name,
            "description": description,
            "published_articles_count": 0,
            "created_at": int(time.time() * 1000),
        })


@pytest.fixture(scope="session")
def guest_client():
    return get_guest_client()


@pytest.fixture(scope="session")
def regular_user_client():
    return get_logged_in_client(regular_user)


@pytest.fixture(scope="session")
def regular_2_user_client():
    return get_logged_in_client(regular_2_user)


@pytest.fixture(scope="session")
def root_user_client():
    return get_logged_in_client(root_user)


user_ids = {}
article_ids = {}


def test_root_user_first_login(root_user_client):
    user_ids["root"] = get_logged_in_user_id(root_user)
    set_dynamodb_user_permissions(user_ids["root"], ["root"])


@pytest.mark.parametrize("user_alias", ["regular", "regular_2"])
def test_regular_user_first_login(request, user_alias):
    get_client(request, user_alias)
    user_data = regular_user if user_alias == "regular" else regular_2_user
    user_ids[user_alias] = get_logged_in_user_id(user_data)


def test_guest_user_get_index(guest_client):
    doc = get_index(guest_client)
    check_header(doc, user_alias=None)
    check_index(doc)


@pytest.mark.parametrize("user_alias", ["regular", "root"])
def test_non_guest_user_get_index(request, user_alias):
    client = get_client(request, user_alias)
    doc = get_index(client)
    check_header(doc, user_alias=user_alias)
    check_index(doc)


@pytest.mark.parametrize("user_alias", ["regular", "root"])
def test_guest_user_get_user(guest_client, user_alias):
    doc = get_user(guest_client, user_alias)
    check_header(doc, user_alias=None)
    check_user(doc, followers_count=0, following_count=0, follow_control=False, block_control=False, user_alias=None,
               activate_control=False, ban_control=False)
    check_articles(doc, articles_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                   popular_control=False, article_aliases=list(article_ids.keys()), css_id="articles")


@pytest.mark.parametrize("user_alias", ["regular_2", "root"])
def test_regular_user_get_other_user(regular_user_client, user_alias):
    doc = get_user(regular_user_client, user_alias)
    check_header(doc, user_alias="regular")
    check_user(doc, followers_count=0, following_count=0, follow_control=True, block_control=True, user_alias=None,
               activate_control=False, ban_control=False)
    check_articles(doc, articles_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                   popular_control=False, article_aliases=list(article_ids.keys()), css_id="articles")


def test_regular_user_get_self_user(regular_user_client):
    user_alias = "regular"
    doc = get_user(regular_user_client, user_alias)
    check_header(doc, user_alias=user_alias)
    check_user(doc, followers_count=0, following_count=0, follow_control=False, block_control=False,
               user_alias=user_alias, activate_control=False, ban_control=False)
    check_articles(doc, articles_count=0, unpublished_control=True, rejected_control=True, popular_control=False,
                   tags_control=False, article_aliases=list(article_ids.keys()), css_id="articles")


def test_root_user_get_user(root_user_client):
    user_alias = "regular"
    doc = get_user(root_user_client, user_alias)
    check_header(doc, user_alias="root")
    check_user(doc, followers_count=0, following_count=0, follow_control=True, block_control=True,
               user_alias=user_alias, activate_control=False, ban_control=True)
    check_articles(doc, articles_count=0, unpublished_control=True, rejected_control=True, popular_control=False,
                   tags_control=False, article_aliases=list(article_ids.keys()), css_id="articles")


def test_guest_user_get_users(guest_client):
    doc = get_users(guest_client)
    check_users(doc, users_count=3, banned_control=False, popular_control=True, user_aliases=list(user_ids.keys()),
                css_id="users")


def test_regular_user_get_users(regular_user_client):
    doc = get_users(regular_user_client)
    check_users(doc, users_count=3, banned_control=False, popular_control=True, user_aliases=list(user_ids.keys()),
                css_id="users")


def test_root_user_get_users(root_user_client):
    doc = get_users(root_user_client)
    check_users(doc, users_count=3, banned_control=True, popular_control=True, user_aliases=list(user_ids.keys()),
                css_id="users")


def test_guest_user_get_articles(guest_client):
    doc = get_articles(guest_client)
    check_articles(doc, articles_count=0, unpublished_control=False, rejected_control=False, tags_control=True,
                   popular_control=True, article_aliases=list(article_ids.keys()), css_id="articles")


def test_regular_user_get_articles(regular_user_client):
    doc = get_articles(regular_user_client)
    check_articles(doc, articles_count=0, unpublished_control=False, rejected_control=False, tags_control=True,
                   popular_control=True, article_aliases=list(article_ids.keys()), css_id="articles")


def test_root_user_get_articles(root_user_client):
    doc = get_articles(root_user_client)
    check_articles(doc, articles_count=0, unpublished_control=True, rejected_control=True, tags_control=True,
                   popular_control=True, article_aliases=list(article_ids.keys()), css_id="articles")


@pytest.mark.parametrize("user_alias", ["regular", "root"])
def test_get_contacts(request, user_alias):
    client = get_client(request, user_alias)
    doc = get_contacts(client)
    check_header(doc, user_alias=user_alias)


@pytest.mark.parametrize("path", ["/any", "/any/any", "/missing", "/foo/bar"])
def test_not_found(guest_client, path):
    resp = get(guest_client, path)
    assert resp.status_code == 404


@pytest.mark.parametrize("user_alias", ["regular", "root"])
def test_logout(request, user_alias):
    client = get_client(request, user_alias)
    resp = get(client, "/logout", allow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/logout-callback")
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_regular_user_can_create_article_comment():
    comment_user_client = get_logged_in_client({
        "sub": "commenter-sub",
        "iss": "commenter-iss",
        "email": "commenter@example.com",
    })
    article_id = str(uuid.uuid4())
    owner_id = user_ids["root"]
    now = int(time.time() * 1000)

    dynamodb_table.put_item(Item={
        "pk": f"POST#{article_id}",
        "sk": "META",
        "id": article_id,
        "title": "Regular comment permission test article",
        "post_slug": "regular-comment-permission-test-article",
        "user_id": owner_id,
        "content": "Long form article content for integration testing. " * 120,
        "tags": ["testing"],
        "rating_sk": now,
        "status": "published",
        "created_at": now,
        "published_at": now,
        "post_status_pk": "POST#published",
        "post_user_status_pk": f"POST#{owner_id}#published",
        "comments_count": 0,
    })

    resp = post(comment_user_client, f"/articles/{article_id}/comments", json={
        "text": "Regular users should be allowed to comment."
    })

    assert resp.status_code == 200
    assert resp.json().endswith(f"/articles/{article_id}")


def test_index_shows_latest_article_comments(guest_client):
    article_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = int(time.time() * 1000)
    article_title = "Latest comments test article"
    article_ids["latest_comments"] = article_id

    dynamodb_table.put_item(Item={
        "pk": f"POST#{article_id}",
        "sk": "META",
        "id": article_id,
        "title": article_title,
        "post_slug": "latest-comments-test-article",
        "user_id": user_id,
        "content": "Long form article content for integration testing. " * 120,
        "tags": ["testing"],
        "rating_sk": now,
        "status": "published",
        "created_at": now,
        "published_at": now,
        "post_status_pk": "POST#published",
        "post_user_status_pk": f"POST#{user_id}#published",
        "comments_count": 6,
    })

    comment_texts = []
    for i in range(6):
        comment_id = f"{now + i}#{uuid.uuid4()}"
        comment_text = f"Latest comment integration text {i}"
        dynamodb_table.put_item(Item={
            "pk": f"POST#{article_id}",
            "sk": f"COMMENT#{comment_id}",
            "id": comment_id,
            "post_id": article_id,
            "post_comment_pk": "POST_COMMENT",
            "post_title": article_title,
            "comment_post_slug": "latest-comments-test-article",
            "user_id": user_id,
            "user_name": "Comment Author",
            "text": comment_text,
            "created_at": now + i,
        })
        comment_texts.append(comment_text)

    doc = get_index(guest_client)
    check_latest_article_comments(doc, comments_count=3, comment_texts=list(reversed(comment_texts[-3:])))

    comments = [pq(el).text() for el in doc("#latest-article-comments .article-comment").items()]
    assert comment_texts[5] in comments[0]
    assert comment_texts[3] in comments[-1]
    assert comment_texts[0] not in doc("#latest-article-comments").text()
    assert article_title in doc("#latest-article-comments").text()


@pytest.mark.parametrize(("legacy_path", "article_path"), [
    ("/posts", "/articles"),
    ("/post", "/articles"),
    ("/posts/new", "/articles/new"),
    ("/post/new", "/articles/new"),
    ("/posts/example-id", "/articles/example-id"),
    ("/post/example-id", "/articles/example-id"),
    ("/posts/example-id/edit", "/articles/example-id/edit"),
    ("/post/example-id/edit", "/articles/example-id/edit"),
    ("/latest/python/posts", "/latest/python/articles"),
])
def test_legacy_article_page_urls_redirect_to_articles(guest_client, legacy_path, article_path):
    response = get(guest_client, f"{legacy_path}?limit=5", allow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"{article_path}?limit=5"


@pytest.mark.parametrize(("method", "legacy_path", "article_path"), [
    ("get", "/posts-fragment", "/articles-fragment"),
    ("get", "/users/example-id/posts-fragment", "/users/example-id/articles-fragment"),
    ("get", "/post-tags/example-tag/edit", "/tags/example-tag/edit"),
    ("get", "/post-tags", "/tags"),
])
def test_legacy_article_endpoint_urls_preserve_method_and_redirect(
        guest_client, method, legacy_path, article_path):
    request = {"get": get, "post": post, "patch": patch}[method]
    kwargs = {"allow_redirects": False}
    if method != "get":
        kwargs["json"] = {}
    response = request(guest_client, f"{legacy_path}?limit=5", **kwargs)
    assert response.status_code == 308
    assert response.headers["location"] == f"{article_path}?limit=5"


@pytest.mark.parametrize("path", [
    "/",
    "/articles",
    "/contacts",
    "/users",
    "/latest/users",
    "/articles-fragment",
    "/users-fragment",
    "/tags",
    "/tags",
    "/privacy-policy",
    "/rules",
    "/terms-of-service",
    "/earn-with-us",
])
def test_public_read_endpoints_success_and_wrong_method_failure(guest_client, path):
    success = get(guest_client, path)
    assert success.status_code == 200, (path, success.status_code, success.text)

    failure = post(guest_client, path, json={})
    expected_status = 422 if path == "/articles" else 405
    assert failure.status_code == expected_status, (path, failure.status_code, failure.text)


@pytest.mark.parametrize("path, schema_type", [
    ("/", "WebSite"),
    ("/articles", "CollectionPage"),
    ("/users", "CollectionPage"),
    ("/latest/users", "CollectionPage"),
    ("/contacts", "ContactPage"),
    ("/privacy-policy", "WebPage"),
    ("/rules", "WebPage"),
    ("/terms-of-service", "WebPage"),
    ("/earn-with-us", "WebPage"),
])
def test_public_page_seo_schema_and_metadata(guest_client, path, schema_type):
    response = get(guest_client, path)
    assert response.status_code == 200, (path, response.status_code, response.text)
    doc = pq(response.text)
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == schema_type
    assert schema.get("url", "").startswith("http")
    assert doc('link[rel="canonical"]').attr("href").startswith("http")
    assert doc('meta[name="description"]').attr("content")
    assert doc('meta[name="robots"]').attr("content") in {"index, follow", "noindex, follow", "noindex, nofollow"}
    assert all(value is not None for value in schema.values())
    check_auth_links_are_nofollow(doc)
    if schema_type in {"CollectionPage", "ContactPage", "WebPage"}:
        assert schema.get("breadcrumb", {}).get("itemListElement")


@pytest.mark.parametrize(("path", "heading", "selector"), [
    ("/", "SysDesPro", "h1"),
    ("/articles", "Articles", "h1"),
    ("/users", "Users", "h1"),
    ("/latest/users", "Users", "h1"),
    ("/contacts", "Contacts", "#contact-form"),
    ("/privacy-policy", "Privacy", "h1"),
    ("/rules", "Rules", "h1"),
    ("/terms-of-service", "Terms", "h1"),
    ("/earn-with-us", "Earn", "h1"),
])
def test_public_pages_render_main_content(guest_client, path, heading, selector):
    response = get(guest_client, path)
    assert response.status_code == 200, (path, response.status_code, response.text)

    doc = pq(response.text)
    main = doc("main")
    assert main
    assert heading.lower() in main("h1").text().lower()
    assert main(selector)


@pytest.mark.parametrize("path", [
    "/articles?limit=invalid",
    "/articles?category=Invalid category",
    "/articles-fragment?limit=0",
    "/users?type=invalid",
    "/invalid/users",
    "/users-fragment?status=invalid",
    "/tags?prefix=",
])
def test_public_query_endpoints_reject_invalid_parameters(guest_client, path):
    response = get(guest_client, path)
    assert response.status_code == 422, (path, response.status_code, response.text)


def test_tags_fragment_endpoint_success(guest_client):
    response = get(guest_client, "/tags-fragment?type=latest&limit=6")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(("tag_type", "expected_title", "canonical_suffix"), [
    ("latest", "Latest Tags", "/tags"),
    ("popular", "Popular Tags", "/tags?type=popular"),
])
def test_tags_page_type_is_in_metadata_and_heading(guest_client, tag_type, expected_title, canonical_suffix):
    response = guest_client.get(f"{WEB_TEST_BASE_URL}/tags?type={tag_type}", timeout=30)

    assert response.status_code == 200
    doc = pq(response.text)
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert expected_title in doc("head title").text()
    assert doc("main h1").text() == expected_title
    assert expected_title in doc('meta[name="description"]').attr("content")
    assert schema["name"] == expected_title
    assert schema["url"].endswith(canonical_suffix)
    assert doc('link[rel="canonical"]').attr("href").endswith(canonical_suffix)


@pytest.mark.parametrize("tag_type", ["latest", "popular"])
def test_tags_endpoint_supports_tag_types(guest_client, tag_type):
    response = get(guest_client, f"/tags?type={tag_type}&limit=6")

    assert response.status_code == 200
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert isinstance(response.json(), list)


def test_login_endpoint_success_and_wrong_method_failure(guest_client):
    success = get(guest_client, "/login", allow_redirects=False)
    assert success.status_code in (302, 307)
    assert "location" in success.headers
    assert success.headers["X-Robots-Tag"] == "noindex, nofollow"

    failure = post(guest_client, "/login", json={})
    assert failure.status_code == 405
    assert failure.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_login_callback_success_and_invalid_code_failure():
    success_client = get_logged_in_client({
        "sub": "callback-success-sub",
        "iss": "callback-success-iss",
        "email": "callback-success@example.com",
    })
    assert success_client.cookies.get("token")

    failure = get(get_guest_client(), "/login-callback?code=invalid", allow_redirects=False)
    assert failure.status_code == 400
    assert failure.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_logout_callback_success_and_wrong_method_failure(guest_client):
    success = get(guest_client, "/logout-callback", allow_redirects=False)
    assert success.status_code in (302, 307)
    assert success.headers["X-Robots-Tag"] == "noindex, nofollow"

    failure = post(guest_client, "/logout-callback", json={})
    assert failure.status_code == 405
    assert failure.headers["X-Robots-Tag"] == "noindex, nofollow"


functional_state = {}


def test_user_edit_update_and_fragment_endpoints_success_and_failure(root_user_client, guest_client):
    root_user_client = get_logged_in_client(root_user)
    root_id = get_dynamodb_user_by_email(root_user["email"])["id"]
    functional_state["root_id"] = root_id

    edit_success = get(root_user_client, f"/users/{root_id}/edit")
    assert edit_success.status_code == 200
    edit_failure = get(guest_client, f"/users/{root_id}/edit")
    assert edit_failure.status_code == 401

    update_success = patch(root_user_client, f"/users/{root_id}", json={
        "name": "Root Functional User",
        "username": "root-functional",
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(root_user_client, f"/users/{root_id}", json={"name": ""})
    assert update_failure.status_code == 422

    fragment_success = get(guest_client, f"/users/{root_id}/articles-fragment")
    assert fragment_success.status_code == 200
    user_read_failure = get(guest_client, "/users/missing-user")
    assert user_read_failure.status_code == 404

    fragment_failure = get(guest_client, "/users/missing-user/articles-fragment")
    assert fragment_failure.status_code == 404

    slug_success = get(guest_client, "/@root-functional")
    assert slug_success.status_code == 200
    slug_failure = get(guest_client, "/@missing-functional-user")
    assert slug_failure.status_code == 404


def test_user_impression_endpoint_success_and_validation_failure(regular_user_client):
    regular_user_client = get_logged_in_client(regular_user)
    target_id = get_dynamodb_user_by_email(regular_2_user["email"])["id"]
    success = post(regular_user_client, f"/users/{target_id}/impression", json={"action": "follow"})
    assert success.status_code == 200, success.text
    failure = post(regular_user_client, f"/users/{target_id}/impression", json={"action": "invalid"})
    assert failure.status_code == 422


def test_user_status_endpoint_success_and_validation_failure(root_user_client):
    root_user_client = get_logged_in_client(root_user)
    target = get_dynamodb_user_by_email("callback-success@example.com")
    success = post(root_user_client, f"/users/{target["id"]}/status", json={
        "status": "banned",
        "comment": "Functional test ban",
    })
    assert success.status_code == 200, success.text
    failure = post(root_user_client, f"/users/{target["id"]}/status", json={"status": "invalid"})
    assert failure.status_code == 422


ARTICLE_IMAGE_FILENAME = "9ba8f5cf-b0a4-430c-99ec-4a78f3c4245f_1080x784.png"
ARTICLE_IMAGE_ALT = "Functional article image"
ARTICLE_CONTENT = ("Functional endpoint coverage content. " * 140
                   + f'<p><img src="/{ARTICLE_IMAGE_FILENAME}" alt="{ARTICLE_IMAGE_ALT}"></p>')


def test_article_create_and_new_page_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)

    new_success = get(root_client, "/articles/new")
    assert new_success.status_code == 200
    new_failure = get(guest_client, "/articles/new")
    assert new_failure.status_code == 401
    check_auth_links_are_nofollow(pq(new_failure.text))

    create_success = post(root_client, "/articles", json={
        "title": "Functional endpoint coverage article",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag", "coverage-tag"],
        "category": "distributed-systems",
    })
    assert create_success.status_code == 200, create_success.text
    article_item = next(
        item for item in dynamodb_table.scan()["Items"]
        if item.get("title") == "Functional endpoint coverage article"
    )
    functional_state["article_id"] = article_item["id"]
    functional_state["article_slug"] = article_item["post_slug"]
    assert article_item["content"].endswith(
        f'<img src="/{ARTICLE_IMAGE_FILENAME}" alt="{ARTICLE_IMAGE_ALT}">')
    assert "figure" not in article_item["content"]
    assert "picture" not in article_item["content"]
    assert article_item["category"] == "distributed-systems"
    assert article_item["post_category_status_pk"] == "POST#distributed-systems#unpublished"

    invalid_links_content = (
            ARTICLE_CONTENT
            + '<a href="/@root-functional/missing-article">Missing article</a>'
            + '<a href="http://web-lambda:5000/@root-functional/missing-article">Missing article again</a>'
    )
    invalid_links = post(root_client, "/articles", json={
        "title": "Article with invalid links",
        "content": invalid_links_content,
        "tags": ["functional-tag"],
        "category": "distributed-systems",
    })
    assert invalid_links.status_code == 422
    content_error = invalid_links.json()["details"]["content"]
    assert "duplicate links" in content_error
    assert "non-existent internal links" in content_error

    create_failure = post(root_client, "/articles", json={
        "title": "a",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag"],
        "category": "distributed-systems",
    })
    assert create_failure.status_code == 422


def test_earn_page_seo(guest_client):
    response = get(guest_client, "/earn-with-us")
    assert response.status_code == 200
    doc = pq(response.text)
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == "WebPage"
    assert schema["breadcrumb"]["itemListElement"][-1]["name"] == "Earn with us"
    assert doc('meta[name="description"]').attr("content").startswith("Learn how to publish")
    assert doc('link[rel="canonical"]').attr("href").endswith("/earn-with-us")


def test_article_read_edit_update_status_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)
    article_id = functional_state["article_id"]

    read_success = get(root_client, f"/articles/{article_id}")
    assert read_success.status_code == 200, read_success.text
    read_doc = pq(read_success.text)
    article_schema = json.loads(read_doc('script[type="application/ld+json"]').text())
    assert article_schema["@type"] == "Article"
    assert article_schema["inLanguage"] == "en"
    assert article_schema["author"]["url"].endswith("/@root-functional")
    assert read_doc('meta[property="og:type"]').attr("content") == "article"
    assert read_doc('meta[property="og:url"]').attr("content").endswith(
        f"/@root-functional/{functional_state['article_slug']}")
    assert not read_doc('meta[name="keywords"]')
    assert "aggregateRating" not in article_schema
    assert article_schema["commentCount"] == 0
    assert article_schema["image"][0].endswith(f"/{ARTICLE_IMAGE_FILENAME}")
    assert article_schema["thumbnailUrl"].endswith(f"/{ARTICLE_IMAGE_FILENAME}")
    assert read_doc('meta[name="robots"]').attr("content") == "index, follow"
    rendered_picture = read_doc("article picture")
    assert len(rendered_picture) == 1
    rendered_source = rendered_picture("source")
    assert len(rendered_source) == 1
    assert rendered_source.attr("type") == "image/webp"
    assert f"{ARTICLE_IMAGE_FILENAME.rsplit('_', 1)[0]}_320x" in rendered_source.attr("srcset")
    assert f"{ARTICLE_IMAGE_FILENAME.rsplit('_', 1)[0]}_640x" in rendered_source.attr("srcset")
    assert f"{ARTICLE_IMAGE_FILENAME.rsplit('_', 1)[0]}_1024x" in rendered_source.attr("srcset")
    rendered_img = rendered_picture("img")
    assert rendered_img.attr("alt") == ARTICLE_IMAGE_ALT
    assert rendered_img.attr("loading") is None
    assert rendered_img.attr("decoding") is None
    assert rendered_img.attr("onerror") is None
    assert rendered_img.attr("data-fallback-src").endswith(f"/{ARTICLE_IMAGE_FILENAME}")
    assert "<figure" in read_success.text

    dynamodb_table.update_item(
        Key={"pk": f"POST#{article_id}", "sk": "META"},
        UpdateExpression="SET image_filename = :filename",
        ExpressionAttributeValues={":filename": "sysdespro_1161x515.png"},
    )
    image_doc = pq(get(root_client, f"/articles/{article_id}").text)
    image_schema = json.loads(image_doc('script[type="application/ld+json"]').text())
    assert image_schema["image"][0].endswith("/sysdespro_1161x515.png")
    assert image_schema["thumbnailUrl"].endswith("/sysdespro_1161x515.png")
    assert image_doc('meta[property="og:image"]').attr("content").endswith("/sysdespro_1161x515.png")
    dynamodb_table.update_item(
        Key={"pk": f"POST#{article_id}", "sk": "META"},
        UpdateExpression="REMOVE image_filename",
    )

    read_failure = get(guest_client, "/articles/missing-article")
    assert read_failure.status_code == 404

    edit_success = get(root_client, f"/articles/{article_id}/edit")
    assert edit_success.status_code == 200
    edit_doc = pq(edit_success.text)
    raw_editor_content = edit_doc("textarea.editor").text()
    assert f'<img src="/{ARTICLE_IMAGE_FILENAME}" alt="{ARTICLE_IMAGE_ALT}">' in raw_editor_content
    assert "<picture>" not in raw_editor_content
    edit_failure = get(regular_client, f"/articles/{article_id}/edit")
    assert edit_failure.status_code == 403

    invalid_links_content = (
            ARTICLE_CONTENT
            + '<a href="/rules">Rules one</a>'
            + '<a href="http://web-lambda:5000/rules">Rules two</a>'
            + '<a href="/root-functional/missing-article">Missing article</a>'
    )
    invalid_links = patch(root_client, f"/articles/{article_id}", json={"content": invalid_links_content})
    assert invalid_links.status_code == 422
    content_error = invalid_links.json()["details"]["content"]
    assert "duplicate links" in content_error
    assert "non-existent internal links" in content_error

    update_success = patch(root_client, f"/articles/{article_id}", json={
        "title": "Updated functional endpoint coverage article",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag", "coverage-tag"],
    })
    assert update_success.status_code == 200, update_success.text
    functional_state["article_slug"] = "updated-functional-endpoint-coverage-article"
    update_failure = patch(root_client, f"/articles/{article_id}", json={
        "title": "a",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag"],
    })
    assert update_failure.status_code == 422

    status_success = post(root_client, f"/articles/{article_id}/status", json={"status": "published"})
    assert status_success.status_code == 200, status_success.text

    published_tag_page = get(guest_client, "/articles?type=latest&status=published&tags=functional-tag")
    assert published_tag_page.status_code == 200
    assert "Updated functional endpoint coverage article" in pq(published_tag_page.text)("#articles").text()

    remove_tag_success = patch(root_client, f"/articles/{article_id}", json={
        "tags": ["coverage-tag"],
    })
    assert remove_tag_success.status_code == 200, remove_tag_success.text
    removed_tag_page = get(guest_client, "/articles?type=latest&status=published&tags=functional-tag")
    assert removed_tag_page.status_code == 200
    assert "Updated functional endpoint coverage article" not in pq(removed_tag_page.text)("#articles").text()

    republish_success = post(root_client, f"/articles/{article_id}/status", json={"status": "published"})
    assert republish_success.status_code == 200, republish_success.text
    current_tag_page = get(guest_client, "/articles?type=latest&status=published&tags=coverage-tag")
    assert current_tag_page.status_code == 200
    assert "Updated functional endpoint coverage article" in pq(current_tag_page.text)("#articles").text()
    stale_tag_page = get(guest_client, "/articles?type=latest&status=published&tags=functional-tag")
    assert stale_tag_page.status_code == 200
    assert "Updated functional endpoint coverage article" not in pq(stale_tag_page.text)("#articles").text()

    restore_tags_success = patch(root_client, f"/articles/{article_id}", json={
        "tags": ["functional-tag", "coverage-tag"],
    })
    assert restore_tags_success.status_code == 200, restore_tags_success.text
    restore_publish_success = post(root_client, f"/articles/{article_id}/status", json={"status": "published"})
    assert restore_publish_success.status_code == 200, restore_publish_success.text

    rename_tag_success = patch(root_client, "/tags/coverage-tag", json={
        "name": "Coverage Tag Updated",
        "image_action": "keep",
        "image_file": None,
    })
    assert rename_tag_success.status_code == 200, rename_tag_success.text
    renamed_old_tag_page = get(
        guest_client,
        "/articles?type=latest&status=published&tags=coverage-tag",
        allow_redirects=False,
    )
    assert renamed_old_tag_page.status_code == 200
    assert "Updated functional endpoint coverage article" in pq(renamed_old_tag_page.text)("#articles").text()
    immutable_tag_slug = patch(root_client, "/tags/coverage-tag", json={"slug": "coverage-tag-updated"})
    assert immutable_tag_slug.status_code == 422

    category_article_page = get(guest_client, "/articles?category=distributed-systems")
    assert category_article_page.status_code == 200
    assert "Updated functional endpoint coverage article" in pq(category_article_page.text)("#articles").text()
    category_tag_article_page = get(
        guest_client,
        "/articles?category=distributed-systems&tags=functional-tag",
    )
    assert category_tag_article_page.status_code == 200
    category_tag_doc = pq(category_tag_article_page.text)
    expected_title = "Latest Functional-Tag Articles in Distributed Systems"
    assert expected_title in category_tag_doc("head title").text()
    assert category_tag_doc("main h1").text() == expected_title
    category_item = dynamodb_table.get_item(
        Key={"pk": "CATEGORY", "sk": "distributed-systems"}
    )["Item"]
    assert category_item["published_articles_count"] == 1

    status_failure = post(root_client, f"/articles/{article_id}/status", json={"status": "invalid"})
    assert status_failure.status_code == 422

    slug_success = get(guest_client, f"/@root-functional/{functional_state["article_slug"]}")
    assert slug_success.status_code == 200, slug_success.text
    slug_failure = get(guest_client, "/@root-functional/missing-article")
    assert slug_failure.status_code == 404

    articles_by_slug_success = get(guest_client, "/root-functional/articles")
    assert articles_by_slug_success.status_code == 200
    articles_by_slug_failure = get(guest_client, "/invalid/latest/articles?limit=0")
    assert articles_by_slug_failure.status_code == 422


def test_categories_page_and_admin_update_endpoints(guest_client):
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)

    page = get(guest_client, "/categories")
    assert page.status_code == 200
    doc = pq(page.text)
    schema = json.loads(doc('script[type="application/ld+json"]').text())
    assert schema["@type"] == "CollectionPage"
    assert len(doc("#categories .card")) > 1
    assert doc('a[href="/articles?category=distributed-systems"]')

    edit_success = get(root_client, "/categories/distributed-systems/edit")
    assert edit_success.status_code == 200
    edit_failure = get(regular_client, "/categories/distributed-systems/edit")
    assert edit_failure.status_code == 403

    name = "Distributed Architecture"
    description = "Design distributed services with explicit consistency and failure trade-offs."
    update_success = patch(root_client, "/categories/distributed-systems", json={
        "name": name,
        "description": description,
        "image_action": "keep",
    })
    assert update_success.status_code == 200, update_success.text
    assert update_success.json().endswith("/categories")
    categories_page = get(guest_client, "/categories").text
    assert name in categories_page
    assert description in categories_page

    immutable_category_slug = patch(root_client, "/categories/distributed-systems", json={
        "slug": "distributed-architecture",
    })
    assert immutable_category_slug.status_code == 422

    update_failure = patch(regular_client, "/categories/distributed-systems", json={
        "description": "Regular users cannot edit category metadata.",
        "image_action": "keep",
    })
    assert update_failure.status_code == 403


def test_article_impression_comment_and_comment_update_endpoints_success_and_failure(guest_client):
    regular_client = get_logged_in_client(regular_user)
    article_id = functional_state["article_id"]

    impression_success = post(regular_client, f"/articles/{article_id}/impression", json={"action": "like"})
    assert impression_success.status_code == 200, impression_success.text
    rated_doc = pq(get(regular_client, f"/articles/{article_id}").text)
    rated_schema = json.loads(rated_doc('script[type="application/ld+json"]').text())
    assert "aggregateRating" not in rated_schema
    assert not rated_doc('meta[name="ratingValue"]')
    assert not rated_doc('meta[name="ratingCount"]')
    impression_failure = post(guest_client, f"/articles/{article_id}/impression", json={"action": "like"})
    assert impression_failure.status_code == 401

    comment_text = "Functional endpoint comment"
    comment_success = post(regular_client, f"/articles/{article_id}/comments", json={"text": comment_text})
    assert comment_success.status_code == 200, comment_success.text
    comment_item = next(
        item for item in dynamodb_table.scan()["Items"]
        if item.get("post_id") == article_id and item.get("text") == comment_text
    )
    comment_id = comment_item["id"]
    encoded_comment_id = quote(comment_id, safe="")
    functional_state["comment_id"] = comment_id
    comment_failure = post(regular_client, f"/articles/{article_id}/comments", json={"text": ""})
    assert comment_failure.status_code == 422

    update_success = patch(regular_client, f"/articles/{article_id}/comments/{encoded_comment_id}", json={
        "text": "Updated functional endpoint comment",
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(regular_client, f"/articles/{article_id}/comments/missing-comment", json={
        "text": "Still valid text",
    })
    assert update_failure.status_code == 404

    comments_doc = pq(get(regular_client, f"/articles/{article_id}").text)
    comments_schema = json.loads(comments_doc('script[type="application/ld+json"]').text())
    assert comments_schema["commentCount"] == 1
    assert len(comments_schema["comment"]) == 1
    assert comments_schema["comment"][0]["text"] == "Updated functional endpoint comment"
    comment_id_fragment = comments_schema["comment"][0]["@id"].rsplit("#", 1)[-1]
    assert comments_doc(f"article#{comment_id_fragment}")
    assert comments_doc(f'time[datetime="{comments_schema["comment"][0]["datePublished"]}"]')

    comments_fragment = get(regular_client, f"/articles/{article_id}/comments-fragment?limit=1")
    assert comments_fragment.status_code == 200, comments_fragment.text
    assert "Updated functional endpoint comment" in comments_fragment.text
    assert "article-comment" in comments_fragment.text
    missing_fragment = get(guest_client, "/articles/missing-article/comments-fragment")
    assert missing_fragment.status_code == 404


def test_public_file_upload_endpoint_success_and_failure(guest_client):
    png_content = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 1100)
    success = post(guest_client, "/public-file", files={
        "file": ("functional.png", png_content, "image/png"),
    })
    assert success.status_code == 200, success.text
    uploaded_path = os.path.join("/app/static", success.json())
    os.remove(uploaded_path)

    failure = post(guest_client, "/public-file", files={
        "file": ("invalid.txt", b"not an image" * 100, "text/plain"),
    })
    assert failure.status_code == 422
    assert failure.headers["content-type"].startswith("application/json")
    assert failure.json()["details"]["file"] == "Invalid image type: None"


def test_public_file_upload_adds_jpeg_dimensions_to_filename(guest_client):
    jpeg_content = (
        b"\xff\xd8"
        b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xc0\x00\x11\x08\x00\x13\x00\x25\x03"
        b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xd9"
    )
    response = post(guest_client, "/public-file", files={
        "file": ("functional.jpg", jpeg_content, "image/jpeg"),
    })

    assert response.status_code == 200, response.text
    assert response.json().endswith("_37x19.jpg")
    os.remove(os.path.join("/app/static", response.json()))


def test_public_file_upload_resizes_wide_image_proportionally(guest_client):
    from io import BytesIO

    from PIL import Image

    original = BytesIO()
    Image.new("RGB", (1600, 800), "red").save(original, format="PNG")

    response = post(guest_client, "/public-file", files={
        "file": ("wide.png", original.getvalue(), "image/png"),
    })

    assert response.status_code == 200, response.text
    assert response.json().endswith("_1200x600.png")
    uploaded_path = os.path.join("/app/static", response.json())
    with Image.open(uploaded_path) as uploaded:
        assert uploaded.size == (1200, 600)
    os.remove(uploaded_path)


def test_public_file_upload_keeps_smaller_image_unchanged(guest_client):
    from io import BytesIO

    from PIL import Image

    original = BytesIO()
    Image.new("RGB", (800, 400), "blue").save(original, format="PNG")
    original_content = original.getvalue()

    response = post(guest_client, "/public-file", files={
        "file": ("small.png", original_content, "image/png"),
    })

    assert response.status_code == 200, response.text
    assert response.json().endswith("_800x400.png")
    uploaded_path = os.path.join("/app/static", response.json())
    with open(uploaded_path, "rb") as uploaded:
        assert uploaded.read() == original_content
    os.remove(uploaded_path)


def test_contact_message_endpoint_success_and_validation_failure(guest_client):
    success = post(guest_client, "/contacts/message", json={
        "name": "Functional Contact",
        "email": "functional-contact@example.com",
        "message": "Functional contact message",
    })
    assert success.status_code == 204, success.text

    failure = post(guest_client, "/contacts/message", json={
        "name": "X",
        "email": "invalid",
        "message": "bad",
    })
    assert failure.status_code == 422


def test_tag_edit_and_update_endpoints_success_and_failure():
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)

    edit_success = get(root_client, "/tags/functional-tag/edit")
    assert edit_success.status_code == 200, edit_success.text
    assert "Slug: functional-tag" in pq(edit_success.text).text()
    edit_failure = get(regular_client, "/tags/functional-tag/edit")
    assert edit_failure.status_code == 403

    update_success = patch(root_client, "/tags/functional-tag", json={
        "name": "Functional Tag Updated",
        "image_action": "keep",
        "image_file": None,
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(root_client, "/tags/functional-tag", json={
        "name": "X",
        "image_action": "keep",
    })
    assert update_failure.status_code == 422
    immutable_slug = patch(root_client, "/tags/functional-tag", json={
        "slug": "functional-tag-updated",
    })
    assert immutable_slug.status_code == 422


def test_admin_page_sitemap_and_cache_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)

    utils_success = get(root_client, "/utils")
    assert utils_success.status_code == 200
    assert 'id="upsert-redirect-form"' in utils_success.text
    utils_failure = get(guest_client, "/utils")
    assert utils_failure.status_code == 401

    redirect_success = post(root_client, "/redirects", json={
        "path": "/legacy-page",
        "redirect_to": "/articles?source=legacy#current",
    })
    assert redirect_success.status_code == 200, redirect_success.text
    assert redirect_success.json() == {
        "path": "/legacy-page",
        "redirect_to": "/articles?source=legacy#current",
    }
    redirected = get(guest_client, "/legacy-page?campaign=test", allow_redirects=False)
    assert redirected.status_code == 308
    assert redirected.headers["location"] == "/articles?source=legacy&campaign=test#current"
    redirect_failure = post(regular_client, "/redirects", json={
        "path": "/another-legacy-page",
        "redirect_to": "/articles",
    })
    assert redirect_failure.status_code == 403
    invalid_redirect = post(root_client, "/redirects", json={
        "path": "/legacy.html",
        "redirect_to": "https://example.com/articles",
    })
    assert invalid_redirect.status_code == 422

    sitemap_success = post(root_client, "/generate-sitemap", json={})
    assert sitemap_success.status_code == 200, sitemap_success.text
    assert sitemap_success.json()["urls_count"] > 0
    assert sitemap_success.json()["sitemap_url"] == f"{os.getenv('WEB_TEST_BASE_URL')}/sitemap.xml"
    sitemap = get(guest_client, "/sitemap.xml")
    assert sitemap.status_code == 200
    assert "/tags" in sitemap.text
    assert "/functional-tag/articles" in sitemap.text
    assert "/popular/functional-tag/articles" in sitemap.text
    assert "/Functional Tag Updated/articles" not in sitemap.text
    sitemap_failure = post(regular_client, "/generate-sitemap", json={})
    assert sitemap_failure.status_code == 403

    cache_success = post(root_client, "/drop-cdn-cache", json={})
    assert cache_success.status_code == 200, cache_success.text
    assert cache_success.json()["success"] is True
    assert cache_success.json()["items_count"] == 1
    cache_paths = post(root_client, "/drop-cdn-cache", json={"paths": ["/articles", "/tags/*"]})
    assert cache_paths.status_code == 200, cache_paths.text
    assert cache_paths.json()["items_count"] == 2
    cache_failure = post(regular_client, "/drop-cdn-cache", json={})
    assert cache_failure.status_code == 403


def test_tag_subscription_create_and_delete():
    root_user_client = get_logged_in_client(root_user)
    tags = ["lifecycle-tag", "lifecycle-combination"]
    response = post(root_user_client, "/tag-subscriptions", json={"tags": tags})
    assert response.status_code == 200, response.text

    fragment = pq(response.text)
    subscription_id = fragment(".tag-subscription-block").attr("data-tag-subscription-id")
    assert subscription_id
    assert "Subscribed" in response.text

    subscriptions = get(root_user_client, "/tag-subscriptions")
    assert subscriptions.status_code == 200
    assert any(item["id"] == subscription_id and item["tags"] == sorted(tags)
               for item in subscriptions.json())

    delete_response = delete(root_user_client, f"/tag-subscriptions/{subscription_id}")
    assert delete_response.status_code == 200, delete_response.text
    assert pq(delete_response.text)(".tag-subscription-block").attr("data-tag-subscription-id") == ""
    assert "Subscribed" not in delete_response.text

    subscriptions = get(root_user_client, "/tag-subscriptions")
    assert all(item["id"] != subscription_id for item in subscriptions.json())


def test_article_published_dispatch_matches_combinations_excludes_author_and_renders_eml():
    root_client = get_logged_in_client(root_user)
    set_dynamodb_user_permissions(get_logged_in_user_id(root_user), ["root"])
    author_client = get_logged_in_client(regular_user)
    combination_client = get_logged_in_client(regular_2_user)
    email_dir = Path("/app/.emails")
    existing_emails = set(email_dir.glob("*.eml"))

    root_subscription = post(root_client, "/tag-subscriptions", json={
        "tags": ["notification-tag3"],
    })
    assert root_subscription.status_code == 200, root_subscription.text
    author_subscription = post(author_client, "/tag-subscriptions", json={
        "tags": ["notification-tag1"],
    })
    assert author_subscription.status_code == 200, author_subscription.text
    combination_subscription = post(combination_client, "/tag-subscriptions", json={
        "tags": ["notification-tag2", "notification-tag3"],
    })
    assert combination_subscription.status_code == 200, combination_subscription.text

    create_response = post(author_client, "/articles", json={
        "title": "Combination notification integration article",
        "content": ARTICLE_CONTENT,
        "tags": ["notification-tag1", "notification-tag2", "notification-tag3"],
        "category": "distributed-systems",
    })
    assert create_response.status_code == 200, create_response.text
    article_id = create_response.json().rstrip("/").split("/")[-1]

    publish_response = post(root_client, f"/articles/{article_id}/status", json={
        "status": "published",
    })
    assert publish_response.status_code == 200, publish_response.text

    new_emails = sorted(set(email_dir.glob("*.eml")) - existing_emails)
    assert len(new_emails) == 2
    messages = {}
    for email_file in new_emails:
        with email_file.open("rb") as stream:
            message = BytesParser(policy=policy.default).parse(stream)
        messages[message["To"]] = message

    assert set(messages) == {"root@example.com", "regular2@example.com"}
    assert "regular@example.com" not in messages

    root_html = messages["root@example.com"].get_body("html").get_content()
    assert f"Hello {get_dynamodb_user_by_email('root@example.com')['name']}" in root_html
    assert "notification-tag3" in root_html
    assert "tags=notification-tag3" in root_html
    assert "notification-tag2 + notification-tag3" not in root_html
    assert "Best regards" in root_html

    combination_html = messages["regular2@example.com"].get_body("html").get_content()
    assert "notification-tag2 + notification-tag3" in combination_html
    assert "tags=notification-tag2&amp;tags=notification-tag3" in combination_html
    assert "Read article" in combination_html


def test_logout_endpoint_wrong_method_failure(guest_client):
    failure = post(guest_client, "/logout", json={})
    assert failure.status_code == 405


def test_legacy_slug_urls_redirect_only_for_existing_entities(guest_client):
    article_id = functional_state["article_id"]
    article_slug = functional_state["article_slug"]
    for legacy_path, canonical_path in [
        ("/root-functional", "/@root-functional"),
        (f"/root-functional/{article_slug}", f"/@root-functional/{article_slug}"),
        (f"/root-functional/posts/{article_id}", f"/@root-functional/{article_slug}"),
        (f"/root-functional/root-functional/{article_slug}",
         f"/@root-functional/{article_slug}"),
    ]:
        response = get(guest_client, f"{legacy_path}?limit=5&offset=2", allow_redirects=False)
        assert response.status_code == 308
        assert response.headers["Location"].endswith(f"{canonical_path}?limit=5&offset=2")

    for path in [
        "/missing-functional-user",
        "/root-functional/missing-functional-article",
        f"/missing-functional-user/{article_slug}",
        "/root-functional/posts/missing-functional-article",
        f"/missing-functional-user/posts/{article_id}",
        f"/root-functional/different-user/{article_slug}",
    ]:
        response = get(guest_client, path, allow_redirects=False)
        assert response.status_code == 404
        assert "Location" not in response.headers


@pytest.mark.parametrize("path", [
    "/articles", "/articles-fragment", "/latest/articles",
    "/users", "/latest/users", "/users-fragment", "/tags",
    "/@root-functional",
])
def test_query_endpoints_ignore_undeclared_parameters(guest_client, path):
    response = get(guest_client, f"{path}?asdasd=13sd&limit=5")
    assert response.status_code == 200, (path, response.status_code, response.text)


@pytest.mark.parametrize("path", [
    "/articles", "/articles-fragment", "/latest/articles",
    "/users", "/latest/users", "/users-fragment", "/tags",
    "/@root-functional",
])
def test_query_endpoints_validate_declared_parameters_with_unknown_parameters(guest_client, path):
    response = get(guest_client, f"{path}?asdasd=13sd&limit=invalid")
    assert response.status_code == 422, (path, response.status_code, response.text)
