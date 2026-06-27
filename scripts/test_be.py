#!/usr/bin/env python3

import time
import uuid

import pytest
from pyquery import PyQuery as pq
from test_utils import (
    recreate_dynamodb_table,
    get_guest_client,
    get_logged_in_client,
    get,
    post,
    regular_user,
    regular_2_user,
    root_user,
    set_dynamodb_user_permissions,
    get_dynamodb_user,
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


def get_me(client):
    resp = get(client, "/me")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    return data["id"]


def get_index(client):
    resp = get(client, "/")
    assert resp.status_code == 200
    return pq(resp.text)


def get_users(client):
    resp = get(client, "/users")
    assert resp.status_code == 200
    doc = pq(resp.text)
    assert "users" in doc("head title").text()
    main_el = doc("main")
    assert "users" in main_el("h1").text()
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
    assert "posts" in doc("head title").text()
    main_el = doc("main")
    assert "posts" in main_el("h1").text()
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
    unpublished_el = main_el('a[href*="status=unpublished"]')
    if unpublished_control:
        assert unpublished_el
    else:
        assert not unpublished_el
    rejected_el = main_el('a[href*="status=rejected"]')
    if rejected_control:
        assert rejected_el
    else:
        assert not rejected_el
    tags_el = main_el('#tags-input')
    if tags_control:
        assert tags_el
    else:
        assert not tags_el
    popular_el = main_el('a[href*="type=popular"]')
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
    banned_el = main_el('a[href*="status=banned"]')
    if banned_control:
        assert banned_el
    else:
        assert not banned_el
    popular_el = main_el('a[href*="type=popular"]')
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
    user_ids["root"] = get_me(root_user_client)
    set_dynamodb_user_permissions(user_ids["root"], ["root"])


@pytest.mark.parametrize("user_alias", ["regular", "regular_2"])
def test_regular_user_first_login(request, user_alias):
    client = get_client(request, user_alias)
    user_ids[user_alias] = get_me(client)


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
    resp = get(client, "/logout")
    assert resp.status_code == 200


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

