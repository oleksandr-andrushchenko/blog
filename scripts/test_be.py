#!/usr/bin/env python3

import time
import os
import uuid

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
    regular_user,
    regular_2_user,
    root_user,
    set_dynamodb_user_permissions,
    get_dynamodb_user,
    get_dynamodb_user_by_email,
    get_dynamodb_post,
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


def get_posts(client):
    resp = get(client, "/posts")
    assert resp.status_code == 200
    doc = pq(resp.text)
    assert "posts" in doc("head title").text().lower()
    main_el = doc("main")
    assert "posts" in main_el("h1").text().lower()
    return doc


def get_post_by_id(client, post):
    resp = get(client, f"/posts/{post['id']}")
    assert resp.status_code == 200
    return pq(resp.text)


def get_post_by_slug(client, post):
    resp = get(client, f"/{post['user_slug']}/{post['slug']}")
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


def get_post_href(post: dict, user: dict | None = None) -> str:
    if username := user.get("username"):
        return f"/{username}"
    return f"/users/{user['id']}"


def check_header(doc, user_alias: str | None):
    header_el = doc("header")
    assert header_el('a[href$="/"]')
    assert header_el('a[href$="/posts"]')
    assert header_el('a[href$="/users"]')
    assert header_el('a[href$="/contacts"]')
    if user_alias:
        assert header_el('a[href$="/posts/new"]')
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


def check_posts(doc, posts_count: int, unpublished_control: bool, rejected_control: bool, tags_control: bool,
                popular_control: bool, post_aliases: list[str], css_id="posts"):
    main_el = doc("main")
    posts_el = main_el("#" + css_id)
    if posts_count:
        assert len(posts_el(".post")) == posts_count
        for post_alias in post_aliases:
            post = get_dynamodb_post(post_ids[post_alias])
            user = get_dynamodb_user(post["user_id"])
            post_el = main_el('a[href$="' + get_post_href(post, user) + '"]')
            assert post_el
            assert post["title"] in post_el.text()
    else:
        assert not posts_el
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


def check_latest_post_comments(doc, comments_count: int, comment_texts: list[str]):
    main_el = doc("main")
    comments_el = main_el("#latest-post-comments")
    if comments_count:
        assert len(comments_el(".latest-post-comment")) == comments_count
        rendered_text = comments_el.text()
        for comment_text in comment_texts:
            assert comment_text in rendered_text
    else:
        assert not comments_el


def check_index(doc):
    check_posts(doc, posts_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                popular_control=False, post_aliases=list(post_ids.keys()), css_id="posts")
    check_posts(doc, posts_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                popular_control=False, post_aliases=list(post_ids.keys()), css_id="popular-posts")
    check_latest_post_comments(doc, comments_count=0, comment_texts=[])
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
post_ids = {}


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
    check_posts(doc, posts_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                popular_control=False, post_aliases=list(post_ids.keys()), css_id="posts")


@pytest.mark.parametrize("user_alias", ["regular_2", "root"])
def test_regular_user_get_other_user(regular_user_client, user_alias):
    doc = get_user(regular_user_client, user_alias)
    check_header(doc, user_alias="regular")
    check_user(doc, followers_count=0, following_count=0, follow_control=True, block_control=True, user_alias=None,
               activate_control=False, ban_control=False)
    check_posts(doc, posts_count=0, unpublished_control=False, rejected_control=False, tags_control=False,
                popular_control=False, post_aliases=list(post_ids.keys()), css_id="posts")


def test_regular_user_get_self_user(regular_user_client):
    user_alias = "regular"
    doc = get_user(regular_user_client, user_alias)
    check_header(doc, user_alias=user_alias)
    check_user(doc, followers_count=0, following_count=0, follow_control=False, block_control=False,
               user_alias=user_alias, activate_control=False, ban_control=False)
    check_posts(doc, posts_count=0, unpublished_control=True, rejected_control=True, popular_control=False,
                tags_control=False, post_aliases=list(post_ids.keys()), css_id="posts")


def test_root_user_get_user(root_user_client):
    user_alias = "regular"
    doc = get_user(root_user_client, user_alias)
    check_header(doc, user_alias="root")
    check_user(doc, followers_count=0, following_count=0, follow_control=True, block_control=True,
               user_alias=user_alias, activate_control=False, ban_control=True)
    check_posts(doc, posts_count=0, unpublished_control=True, rejected_control=True, popular_control=False,
                tags_control=False, post_aliases=list(post_ids.keys()), css_id="posts")


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


def test_guest_user_get_posts(guest_client):
    doc = get_posts(guest_client)
    check_posts(doc, posts_count=0, unpublished_control=False, rejected_control=False, tags_control=True,
                popular_control=True, post_aliases=list(post_ids.keys()), css_id="posts")


def test_regular_user_get_posts(regular_user_client):
    doc = get_posts(regular_user_client)
    check_posts(doc, posts_count=0, unpublished_control=False, rejected_control=False, tags_control=True,
                popular_control=True, post_aliases=list(post_ids.keys()), css_id="posts")


def test_root_user_get_posts(root_user_client):
    doc = get_posts(root_user_client)
    check_posts(doc, posts_count=0, unpublished_control=True, rejected_control=True, tags_control=True,
                popular_control=True, post_aliases=list(post_ids.keys()), css_id="posts")


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


def test_regular_user_can_create_post_comment():
    comment_user_client = get_logged_in_client({
        "sub": "commenter-sub",
        "iss": "commenter-iss",
        "email": "commenter@example.com",
    })
    post_id = str(uuid.uuid4())
    owner_id = user_ids["root"]
    now = int(time.time() * 1000)

    dynamodb_table.put_item(Item={
        "pk": f"POST#{post_id}",
        "sk": "META",
        "id": post_id,
        "title": "Regular comment permission test post",
        "post_slug": "regular-comment-permission-test-post",
        "user_id": owner_id,
        "content": "Long form post content for integration testing. " * 120,
        "tags": ["testing"],
        "rating_sk": now,
        "status": "published",
        "created_at": now,
        "published_at": now,
        "post_status_pk": "POST#published",
        "post_user_status_pk": f"POST#{owner_id}#published",
        "comments_count": 0,
    })

    resp = post(comment_user_client, f"/api/posts/{post_id}/comment", json={
        "text": "Regular users should be allowed to comment."
    })

    assert resp.status_code == 200
    assert resp.json().endswith(f"/posts/{post_id}")


def test_index_shows_latest_post_comments(guest_client):
    post_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = int(time.time() * 1000)
    post_title = "Latest comments test post"
    post_ids["latest_comments"] = post_id

    dynamodb_table.put_item(Item={
        "pk": f"POST#{post_id}",
        "sk": "META",
        "id": post_id,
        "title": post_title,
        "post_slug": "latest-comments-test-post",
        "user_id": user_id,
        "content": "Long form post content for integration testing. " * 120,
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
            "pk": f"POST#{post_id}",
            "sk": f"COMMENT#{comment_id}",
            "id": comment_id,
            "post_id": post_id,
            "post_comment_pk": "POST_COMMENT",
            "post_title": post_title,
            "comment_post_slug": "latest-comments-test-post",
            "user_id": user_id,
            "user_name": "Comment Author",
            "text": comment_text,
            "created_at": now + i,
        })
        comment_texts.append(comment_text)

    doc = get_index(guest_client)
    check_latest_post_comments(doc, comments_count=5, comment_texts=list(reversed(comment_texts[-5:])))

    comments = [pq(el).text() for el in doc("#latest-post-comments .latest-post-comment").items()]
    assert comment_texts[5] in comments[0]
    assert comment_texts[1] in comments[-1]
    assert comment_texts[0] not in doc("#latest-post-comments").text()
    assert post_title in doc("#latest-post-comments").text()



@pytest.mark.parametrize("path", [
    "/",
    "/posts",
    "/contacts",
    "/users",
    "/latest/users",
    "/api/posts-fragment",
    "/api/users-fragment",
    "/api/post-tags",
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
    "/posts?limit=invalid",
    "/api/posts-fragment?limit=0",
    "/users?type=invalid",
    "/invalid/users",
    "/api/users-fragment?status=invalid",
    "/api/post-tags?prefix=",
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

    fragment_success = get(guest_client, f"/api/users/{root_id}/posts-fragment")
    assert fragment_success.status_code == 200
    user_read_failure = get(guest_client, "/users/missing-user")
    assert user_read_failure.status_code == 404

    fragment_failure = get(guest_client, "/api/users/missing-user/posts-fragment")
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



POST_CONTENT = "Functional endpoint coverage content. " * 160


def test_post_create_and_new_page_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)

    new_success = get(root_client, "/posts/new")
    assert new_success.status_code == 200
    new_failure = get(guest_client, "/posts/new")
    assert new_failure.status_code == 401

    create_success = post(root_client, "/api/posts", json={
        "title": "Functional endpoint coverage article",
        "content": POST_CONTENT,
        "tags": ["functional-tag", "coverage-tag"],
    })
    assert create_success.status_code == 200, create_success.text
    post_item = next(
        item for item in dynamodb_table.scan()["Items"]
        if item.get("title") == "Functional endpoint coverage article"
    )
    functional_state["post_id"] = post_item["id"]
    functional_state["post_slug"] = post_item["post_slug"]

    create_failure = post(root_client, "/api/posts", json={
        "title": "short",
        "content": POST_CONTENT,
        "tags": ["functional-tag"],
    })
    assert create_failure.status_code == 422


def test_post_read_edit_update_status_endpoints_success_and_failure(guest_client):
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)
    post_id = functional_state["post_id"]

    read_success = get(root_client, f"/posts/{post_id}")
    assert read_success.status_code == 200, read_success.text
    read_failure = get(guest_client, "/posts/missing-post")
    assert read_failure.status_code == 404

    edit_success = get(root_client, f"/posts/{post_id}/edit")
    assert edit_success.status_code == 200
    edit_failure = get(regular_client, f"/posts/{post_id}/edit")
    assert edit_failure.status_code == 403

    update_success = patch(root_client, f"/api/posts/{post_id}", json={
        "title": "Updated functional endpoint coverage article",
        "content": POST_CONTENT,
        "tags": ["functional-tag", "coverage-tag"],
    })
    assert update_success.status_code == 200, update_success.text
    functional_state["post_slug"] = "updated-functional-endpoint-coverage-article"
    update_failure = patch(root_client, f"/api/posts/{post_id}", json={
        "title": "bad",
        "content": POST_CONTENT,
        "tags": ["functional-tag"],
    })
    assert update_failure.status_code == 422

    status_success = post(root_client, f"/api/posts/{post_id}/status", json={"status": "published"})
    assert status_success.status_code == 200, status_success.text
    status_failure = post(root_client, f"/api/posts/{post_id}/status", json={"status": "invalid"})
    assert status_failure.status_code == 422

    slug_success = get(guest_client, f"/root-functional/{functional_state["post_slug"]}")
    assert slug_success.status_code == 200, slug_success.text
    slug_failure = get(guest_client, "/root-functional/missing-post")
    assert slug_failure.status_code == 404

    posts_by_slug_success = get(guest_client, "/root-functional/posts")
    assert posts_by_slug_success.status_code == 200
    posts_by_slug_failure = get(guest_client, "/invalid/latest/posts?limit=0")
    assert posts_by_slug_failure.status_code == 422


def test_post_impression_comment_and_comment_update_endpoints_success_and_failure(guest_client):
    regular_client = get_logged_in_client(regular_user)
    post_id = functional_state["post_id"]

    impression_success = post(regular_client, f"/api/posts/{post_id}/impression", json={"action": "like"})
    assert impression_success.status_code == 200, impression_success.text
    impression_failure = post(guest_client, f"/api/posts/{post_id}/impression", json={"action": "like"})
    assert impression_failure.status_code == 401

    comment_text = "Functional endpoint comment"
    comment_success = post(regular_client, f"/api/posts/{post_id}/comment", json={"text": comment_text})
    assert comment_success.status_code == 200, comment_success.text
    comment_item = next(
        item for item in dynamodb_table.scan()["Items"]
        if item.get("post_id") == post_id and item.get("text") == comment_text
    )
    comment_id = comment_item["id"]
    encoded_comment_id = quote(comment_id, safe="")
    functional_state["comment_id"] = comment_id
    comment_failure = post(regular_client, f"/api/posts/{post_id}/comment", json={"text": ""})
    assert comment_failure.status_code == 422

    update_success = patch(regular_client, f"/api/posts/{post_id}/comments/{encoded_comment_id}", json={
        "text": "Updated functional endpoint comment",
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(regular_client, f"/api/posts/{post_id}/comments/missing-comment", json={
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


def test_post_tag_edit_and_update_endpoints_success_and_failure():
    root_client = get_logged_in_client(root_user)
    regular_client = get_logged_in_client(regular_user)

    edit_success = get(root_client, "/post-tags/functional-tag/edit")
    assert edit_success.status_code == 200, edit_success.text
    edit_failure = get(regular_client, "/post-tags/functional-tag/edit")
    assert edit_failure.status_code == 403

    update_success = patch(root_client, "/api/post-tags/functional-tag", json={
        "name": "Functional Tag Updated",
        "image_action": "keep",
        "image_file": None,
    })
    assert update_success.status_code == 200, update_success.text
    update_failure = patch(root_client, "/api/post-tags/functional-tag-updated", json={
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


def test_logout_endpoint_wrong_method_failure(guest_client):
    failure = post(guest_client, "/logout", json={})
    assert failure.status_code == 405

