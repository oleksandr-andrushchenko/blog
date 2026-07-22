#!/usr/bin/env python3

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
    main_el = doc("main")
    assert user["name"] in main_el("h1").text()
    return doc


def get_logged_in_user_id(user_data: dict) -> str:
    return get_dynamodb_user_by_email(user_data["email"])["id"]


def get_index(client):
    resp = get(client, "/")
    assert resp.status_code == 200
    return pq(resp.text)


def get_users(client):
    resp = get(client, "/users")
    assert resp.status_code == 200
    doc = pq(resp.text)
    assert "users" in doc("head title").text().lower()
    main_el = doc("main")
    assert "users" in main_el("h1").text().lower()
    return doc


def get_user_by_id(client, user):
    resp = get(client, f"/users/{user['id']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_user_by_slug(client, user):
    resp = get(client, f"/{user['username']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_articles(client):
    resp = get(client, "/articles")
    assert resp.status_code == 200
    doc = pq(resp.text)
    assert "articles" in doc("head title").text().lower()
    main_el = doc("main")
    assert "articles" in main_el("h1").text().lower()
    return doc


def get_article_by_id(client, article):
    resp = get(client, f"/articles/{article['id']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_article_by_slug(client, article):
    resp = get(client, f"/{article['user_slug']}/{article['slug']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_contacts(client):
    resp = get(client, "/contacts")
    assert resp.status_code == 200
    return pq(resp.text)


def get_user_href(user: dict) -> str:
    if username := user.get("username"):
        return f"/{username}"
    return f"/users/{user['id']}"


def get_article_href(article: dict, user: dict | None = None) -> str:
    if username := user.get("username"):
        return f"/{username}"
    return f"/users/{user['id']}"


def check_header(doc, user_alias: str | None):
    header_el = doc("header")
    assert header_el('a[href$="/"]')
    assert header_el('a[href$="/articles"]')
    assert header_el('a[href$="/users"]')
    assert header_el('a[href$="/contacts"]')
    if user_alias:
        assert header_el('a[href$="/articles/new"]')
        assert header_el('a[href$="/logout"]')
        user = get_dynamodb_user(user_ids[user_alias])
        assert header_el('a[href$="' + get_user_href(user) + '"]')
    else:
        assert header_el('a[href$="/login"]')
        # todo: "*=" - contains
        user_view_el = header_el('a[href="/users/*"]')
        assert not user_view_el


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
        assert len(comments_el(".latest-article-comment")) == comments_count
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
    resp = get(client, "/contacts")
    assert resp.status_code == 200


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

    resp = post(comment_user_client, f"/api/articles/{article_id}/comment", json={
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
    check_latest_article_comments(doc, comments_count=5, comment_texts=list(reversed(comment_texts[-5:])))

    comments = [pq(el).text() for el in doc("#latest-article-comments .latest-article-comment").items()]
    assert comment_texts[5] in comments[0]
    assert comment_texts[1] in comments[-1]
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
    assert response.status_code == 301
    assert response.headers["location"] == f"{article_path}?limit=5"


@pytest.mark.parametrize(("method", "legacy_path", "article_path"), [
    ("get", "/api/posts-fragment", "/api/articles-fragment"),
    ("get", "/api/users/example-id/posts-fragment", "/api/users/example-id/articles-fragment"),
    ("post", "/api/posts", "/api/articles"),
    ("patch", "/api/posts/example-id", "/api/articles/example-id"),
    ("post", "/api/posts/example-id/status", "/api/articles/example-id/status"),
    ("post", "/api/posts/example-id/impression", "/api/articles/example-id/impression"),
    ("post", "/api/posts/example-id/comment", "/api/articles/example-id/comment"),
    ("patch", "/api/posts/example-id/comments/example-comment-id",
     "/api/articles/example-id/comments/example-comment-id"),
    ("get", "/post-tags/example-tag/edit", "/article-tags/example-tag/edit"),
    ("get", "/api/post-tags", "/api/article-tags"),
    ("patch", "/api/post-tags/example-tag", "/api/article-tags/example-tag"),
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
    "/api/articles-fragment",
    "/api/users-fragment",
    "/api/article-tags",
    "/privacy-policy",
    "/rules",
    "/terms-of-service",
    "/earn-with-us",
])
def test_public_read_endpoints_success_and_wrong_method_failure(guest_client, path):
    success = get(guest_client, path)
    assert success.status_code == 200, (path, success.status_code, success.text)

    failure = post(guest_client, path, json={})
    assert failure.status_code == 405, (path, failure.status_code, failure.text)


@pytest.mark.parametrize("path", [
    "/articles?limit=invalid",
    "/api/articles-fragment?limit=0",
    "/users?type=invalid",
    "/invalid/users",
    "/api/users-fragment?status=invalid",
    "/api/article-tags?prefix=",
])
def test_public_query_endpoints_reject_invalid_parameters(guest_client, path):
    response = get(guest_client, path)
    assert response.status_code == 422, (path, response.status_code, response.text)


def test_login_endpoint_success_and_wrong_method_failure(guest_client):
    success = get(guest_client, "/login", allow_redirects=False)
    assert success.status_code in (302, 307)
    assert "location" in success.headers

    failure = post(guest_client, "/login", json={})
    assert failure.status_code == 405


def test_login_callback_success_and_invalid_code_failure():
    success_client = get_logged_in_client({
        "sub": "callback-success-sub",
        "iss": "callback-success-iss",
        "email": "callback-success@example.com",
    })
    assert success_client.cookies.get("token")

    failure = get(get_guest_client(), "/login-callback?code=invalid", allow_redirects=False)
    assert failure.status_code == 400


def test_logout_callback_success_and_wrong_method_failure(guest_client):
    success = get(guest_client, "/logout-callback", allow_redirects=False)
    assert success.status_code in (302, 307)

    failure = post(guest_client, "/logout-callback", json={})
    assert failure.status_code == 405


functional_state = {}


def test_user_edit_update_and_fragment_endpoints_success_and_failure(root_user_client, guest_client):
    root_user_client = get_logged_in_client(root_user)
    root_id = get_dynamodb_user_by_email(root_user["email"])["id"]
    functional_state["root_id"] = root_id

    edit_success = get(root_user_client, f"/users/{root_id}/edit")
    assert edit_success.status_code == 200
    edit_failure = get(guest_client, f"/users/{root_id}/edit")
    assert edit_failure.status_code == 401

    update_success = patch(root_user_client, f"/api/users/{root_id}", json={
        "name": "Root Functional User",
        "username": "root-functional",
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(root_user_client, f"/api/users/{root_id}", json={"name": ""})
    assert update_failure.status_code == 422

    fragment_success = get(guest_client, f"/api/users/{root_id}/articles-fragment")
    assert fragment_success.status_code == 200
    user_read_failure = get(guest_client, "/users/missing-user")
    assert user_read_failure.status_code == 404

    fragment_failure = get(guest_client, "/api/users/missing-user/articles-fragment")
    assert fragment_failure.status_code == 404

    slug_success = get(guest_client, "/root-functional")
    assert slug_success.status_code == 200
    slug_failure = get(guest_client, "/missing-functional-user")
    assert slug_failure.status_code == 404


def test_user_impression_endpoint_success_and_validation_failure(regular_user_client):
    regular_user_client = get_logged_in_client(regular_user)
    target_id = get_dynamodb_user_by_email(regular_2_user["email"])["id"]
    success = post(regular_user_client, f"/api/users/{target_id}/impression", json={"action": "follow"})
    assert success.status_code == 200, success.text
    failure = post(regular_user_client, f"/api/users/{target_id}/impression", json={"action": "invalid"})
    assert failure.status_code == 422


def test_user_status_endpoint_success_and_validation_failure(root_user_client):
    root_user_client = get_logged_in_client(root_user)
    target = get_dynamodb_user_by_email("callback-success@example.com")
    success = post(root_user_client, f"/api/users/{target["id"]}/status", json={
        "status": "banned",
        "comment": "Functional test ban",
    })
    assert success.status_code == 200, success.text
    failure = post(root_user_client, f"/api/users/{target["id"]}/status", json={"status": "invalid"})
    assert failure.status_code == 422


ARTICLE_CONTENT = "Functional endpoint coverage content. " * 160


def test_article_create_and_new_page_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)

    new_success = get(root_client, "/articles/new")
    assert new_success.status_code == 200
    new_failure = get(guest_client, "/articles/new")
    assert new_failure.status_code == 401

    create_success = post(root_client, "/api/articles", json={
        "title": "Functional endpoint coverage article",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag", "coverage-tag"],
    })
    assert create_success.status_code == 200, create_success.text
    article_item = next(
        item for item in dynamodb_table.scan()["Items"]
        if item.get("title") == "Functional endpoint coverage article"
    )
    functional_state["article_id"] = article_item["id"]
    functional_state["article_slug"] = article_item["post_slug"]

    create_failure = post(root_client, "/api/articles", json={
        "title": "short",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag"],
    })
    assert create_failure.status_code == 422


def test_article_read_edit_update_status_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)
    article_id = functional_state["article_id"]

    read_success = get(root_client, f"/articles/{article_id}")
    assert read_success.status_code == 200, read_success.text
    read_failure = get(guest_client, "/articles/missing-article")
    assert read_failure.status_code == 404

    edit_success = get(root_client, f"/articles/{article_id}/edit")
    assert edit_success.status_code == 200
    edit_failure = get(regular_client, f"/articles/{article_id}/edit")
    assert edit_failure.status_code == 403

    update_success = patch(root_client, f"/api/articles/{article_id}", json={
        "title": "Updated functional endpoint coverage article",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag", "coverage-tag"],
    })
    assert update_success.status_code == 200, update_success.text
    functional_state["article_slug"] = "updated-functional-endpoint-coverage-article"
    update_failure = patch(root_client, f"/api/articles/{article_id}", json={
        "title": "bad",
        "content": ARTICLE_CONTENT,
        "tags": ["functional-tag"],
    })
    assert update_failure.status_code == 422

    status_success = post(root_client, f"/api/articles/{article_id}/status", json={"status": "published"})
    assert status_success.status_code == 200, status_success.text
    status_failure = post(root_client, f"/api/articles/{article_id}/status", json={"status": "invalid"})
    assert status_failure.status_code == 422

    slug_success = get(guest_client, f"/root-functional/{functional_state["article_slug"]}")
    assert slug_success.status_code == 200, slug_success.text
    slug_failure = get(guest_client, "/root-functional/missing-article")
    assert slug_failure.status_code == 404

    articles_by_slug_success = get(guest_client, "/root-functional/articles")
    assert articles_by_slug_success.status_code == 200
    articles_by_slug_failure = get(guest_client, "/invalid/latest/articles?limit=0")
    assert articles_by_slug_failure.status_code == 422


def test_article_impression_comment_and_comment_update_endpoints_success_and_failure(guest_client):
    regular_client = get_logged_in_client(regular_user)
    article_id = functional_state["article_id"]

    impression_success = post(regular_client, f"/api/articles/{article_id}/impression", json={"action": "like"})
    assert impression_success.status_code == 200, impression_success.text
    impression_failure = post(guest_client, f"/api/articles/{article_id}/impression", json={"action": "like"})
    assert impression_failure.status_code == 401

    comment_text = "Functional endpoint comment"
    comment_success = post(regular_client, f"/api/articles/{article_id}/comment", json={"text": comment_text})
    assert comment_success.status_code == 200, comment_success.text
    comment_item = next(
        item for item in dynamodb_table.scan()["Items"]
        if item.get("post_id") == article_id and item.get("text") == comment_text
    )
    comment_id = comment_item["id"]
    encoded_comment_id = quote(comment_id, safe="")
    functional_state["comment_id"] = comment_id
    comment_failure = post(regular_client, f"/api/articles/{article_id}/comment", json={"text": ""})
    assert comment_failure.status_code == 422

    update_success = patch(regular_client, f"/api/articles/{article_id}/comments/{encoded_comment_id}", json={
        "text": "Updated functional endpoint comment",
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(regular_client, f"/api/articles/{article_id}/comments/missing-comment", json={
        "text": "Still valid text",
    })
    assert update_failure.status_code == 404


def test_public_file_upload_endpoint_success_and_failure(guest_client):
    png_content = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 1100)
    success = post(guest_client, "/api/public-file", files={
        "file": ("functional.png", png_content, "image/png"),
    })
    assert success.status_code == 200, success.text
    uploaded_path = os.path.join("/app/be-function-src/static", success.json())
    os.remove(uploaded_path)

    failure = post(guest_client, "/api/public-file", files={
        "file": ("invalid.txt", b"not an image" * 100, "text/plain"),
    })
    assert failure.status_code == 422


def test_contact_message_endpoint_success_and_validation_failure(guest_client):
    success = post(guest_client, "/api/contacts/message", json={
        "name": "Functional Contact",
        "email": "functional-contact@example.com",
        "message": "Functional contact message",
    })
    assert success.status_code == 204, success.text

    failure = post(guest_client, "/api/contacts/message", json={
        "name": "X",
        "email": "invalid",
        "message": "bad",
    })
    assert failure.status_code == 422


def test_article_tag_edit_and_update_endpoints_success_and_failure():
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)

    edit_success = get(root_client, "/article-tags/functional-tag/edit")
    assert edit_success.status_code == 200, edit_success.text
    edit_failure = get(regular_client, "/article-tags/functional-tag/edit")
    assert edit_failure.status_code == 403

    update_success = patch(root_client, "/api/article-tags/functional-tag", json={
        "name": "Functional Tag Updated",
        "image_action": "keep",
        "image_file": None,
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(root_client, "/api/article-tags/functional-tag-updated", json={
        "name": "X",
        "image_action": "keep",
    })
    assert update_failure.status_code == 422


def test_admin_page_sitemap_and_cache_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)

    utils_success = get(root_client, "/utils")
    assert utils_success.status_code == 200
    utils_failure = get(guest_client, "/utils")
    assert utils_failure.status_code == 401

    sitemap_success = post(root_client, "/api/generate-sitemap", json={})
    assert sitemap_success.status_code == 200, sitemap_success.text
    assert sitemap_success.json()["urls_count"] > 0
    sitemap = get(guest_client, "/sitemap.xml")
    assert sitemap.status_code == 200
    assert "/functional-tag-updated/articles" in sitemap.text
    assert "/popular/functional-tag-updated/articles" in sitemap.text
    assert "/Functional Tag Updated/articles" not in sitemap.text
    sitemap_failure = post(regular_client, "/api/generate-sitemap", json={})
    assert sitemap_failure.status_code == 403

    cache_success = post(root_client, "/api/drop-cdn-cache", json={})
    assert cache_success.status_code == 200, cache_success.text
    assert cache_success.json()["success"] is True
    cache_failure = post(regular_client, "/api/drop-cdn-cache", json={})
    assert cache_failure.status_code == 403


def test_dummy_fixtures_endpoint_success_and_wrong_method_failure(guest_client):
    success = post(guest_client, "/api/dummy-fixtures", json={})
    assert success.status_code == 200, success.text

    failure = get(guest_client, "/api/dummy-fixtures")
    assert failure.status_code in (404, 405)


def test_article_tag_subscription_create_and_delete():
    root_user_client = get_logged_in_client(root_user)
    tags = ["lifecycle-tag", "lifecycle-combination"]
    response = post(root_user_client, "/api/article-tag-subscriptions", json={"tags": tags})
    assert response.status_code == 200, response.text

    fragment = pq(response.text)
    subscription_id = fragment(".article-tag-subscription-block").attr("data-article-tag-subscription-id")
    assert subscription_id
    assert "Unsubscribe" in response.text

    subscriptions = get(root_user_client, "/api/article-tag-subscriptions")
    assert subscriptions.status_code == 200
    assert any(item["id"] == subscription_id and item["tags"] == sorted(tags)
               for item in subscriptions.json())

    delete_response = delete(root_user_client, f"/api/article-tag-subscriptions/{subscription_id}")
    assert delete_response.status_code == 200, delete_response.text
    assert pq(delete_response.text)(".article-tag-subscription-block").attr("data-article-tag-subscription-id") == ""
    assert "Unsubscribe" not in delete_response.text

    subscriptions = get(root_user_client, "/api/article-tag-subscriptions")
    assert all(item["id"] != subscription_id for item in subscriptions.json())


def test_article_published_dispatch_matches_combinations_excludes_author_and_renders_eml():
    root_client = get_logged_in_client(root_user)
    set_dynamodb_user_permissions(get_logged_in_user_id(root_user), ["root"])
    author_client = get_logged_in_client(regular_user)
    combination_client = get_logged_in_client(regular_2_user)
    email_dir = Path("/app/.emails")
    existing_emails = set(email_dir.glob("*.eml"))

    root_subscription = post(root_client, "/api/article-tag-subscriptions", json={
        "tags": ["notification-tag3"],
    })
    assert root_subscription.status_code == 200, root_subscription.text
    author_subscription = post(author_client, "/api/article-tag-subscriptions", json={
        "tags": ["notification-tag1"],
    })
    assert author_subscription.status_code == 200, author_subscription.text
    combination_subscription = post(combination_client, "/api/article-tag-subscriptions", json={
        "tags": ["notification-tag2", "notification-tag3"],
    })
    assert combination_subscription.status_code == 200, combination_subscription.text

    create_response = post(author_client, "/api/articles", json={
        "title": "Combination notification integration article",
        "content": ARTICLE_CONTENT,
        "tags": ["notification-tag1", "notification-tag2", "notification-tag3"],
    })
    assert create_response.status_code == 200, create_response.text
    article_id = create_response.json().rstrip("/").split("/")[-1]

    publish_response = post(root_client, f"/api/articles/{article_id}/status", json={
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
