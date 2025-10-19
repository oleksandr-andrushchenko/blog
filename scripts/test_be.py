#!/usr/bin/env python3

import pytest
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
)


def get_me(client):
    resp = get(client, "/me")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    return data["id"]


def get_index(client):
    resp = get(client, "/")
    assert resp.status_code == 200
    return resp.text


def get_users(client):
    resp = get(client, "/users")
    assert resp.status_code == 200
    return resp.text


def get_user_by_id(client, user):
    resp = get(client, f"/users/{user['id']}")
    assert resp.status_code == 200
    return resp.text


def get_user_by_slug(client, user):
    resp = get(client, f"/{user['username']}")
    assert resp.status_code == 200
    return resp.text


def get_posts(client):
    resp = get(client, "/posts")
    assert resp.status_code == 200
    return resp.text


def get_post_by_id(client, post):
    resp = get(client, f"/posts/{post['id']}")
    assert resp.status_code == 200
    return resp.text


def get_post_by_slug(client, post):
    resp = get(client, f"/{post['user_slug']}/{post['slug']}")
    assert resp.status_code == 200
    return resp.text


def get_contacts(client):
    resp = get(client, "/contacts")
    assert resp.status_code == 200
    return resp.text


def has_general_controls(content: str):
    assert "lnk-index" in content
    assert "lnk-posts" in content
    assert "lnk-users" in content
    assert "lnk-contacts" in content


def has_logged_in_controls(content: str):
    assert "lnk-logout" in content
    assert "lnk-create-post" in content


def has_not_logged_in_controls(content: str):
    assert "lnk-logout" not in content
    assert "lnk-create-post" not in content


def has_logged_out_controls(content: str):
    assert "lnk-login" in content


def has_not_logged_out_controls(content: str):
    assert "lnk-login" not in content


def get_client(request, user):
    client_name = f"{user}_user_client"
    client = request.getfixturevalue(client_name)
    return client


def get_user(client, user) -> str:
    user_id = user_ids[user]
    ddb_user = get_dynamodb_user(user_id)
    expected_name = ddb_user["name"]

    resp = get(client, f"/users/{user_id}")
    assert resp.status_code == 200

    content = resp.text
    assert expected_name in content
    return content


def has_user_impression_control(content: str):
    assert "btn-user-follow" in content
    assert "btn-user-block" in content


def has_not_user_impression_control(content: str):
    assert "btn-user-follow" not in content
    assert "btn-user-block" not in content


def has_user_edit_control(content: str):
    assert "btn-user-edit" in content


def has_not_user_edit_control(content: str):
    assert "btn-user-edit" not in content


def has_user_ban_control(content: str):
    assert "btn-user-ban" in content


def has_not_user_ban_control(content: str):
    assert "btn-user-ban" not in content


def has_user_posts_filter_control(content: str):
    assert "blk-posts-filter" in content


def has_not_user_posts_filter_control(content: str):
    assert "blk-posts-filter" not in content


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


def test_root_user_first_login(root_user_client):
    user_ids["root"] = get_me(root_user_client)
    set_dynamodb_user_permissions(user_ids["root"], ["root"])


@pytest.mark.parametrize("user", ["regular", "regular_2"])
def test_regular_user_first_login(request, user):
    client = get_client(request, user)
    user_ids[user] = get_me(client)


def test_guest_user_get_index(guest_client):
    content = get_index(guest_client)
    has_general_controls(content)
    has_logged_out_controls(content)
    has_not_logged_in_controls(content)


@pytest.mark.parametrize("user", ["regular", "root"])
def test_reg_user_get_index(request, user):
    client = get_client(request, user)
    content = get_index(client)
    has_general_controls(content)
    has_not_logged_out_controls(content)
    has_logged_in_controls(content)


@pytest.mark.parametrize("user", ["regular", "root"])
def test_guest_user_get_user(guest_client, user):
    content = get_user(guest_client, user)
    has_general_controls(content)
    has_not_logged_in_controls(content)
    has_logged_out_controls(content)
    has_not_user_impression_control(content)


@pytest.mark.parametrize("user", ["regular_2", "root"])
def test_regular_user_get_other_user(regular_user_client, user):
    content = get_user(regular_user_client, user)
    has_general_controls(content)
    has_logged_in_controls(content)
    has_not_logged_out_controls(content)
    has_user_impression_control(content)
    has_not_user_edit_control(content)
    has_not_user_ban_control(content)
    has_not_user_posts_filter_control(content)


def test_regular_user_get_self_user(regular_user_client):
    content = get_user(regular_user_client, "regular")
    has_general_controls(content)
    has_not_user_impression_control(content)
    has_user_edit_control(content)
    has_not_user_ban_control(content)
    has_user_posts_filter_control(content)


def test_root_user_get_user(root_user_client):
    content = get_user(root_user_client, "regular")
    has_general_controls(content)
    has_user_impression_control(content)
    has_user_edit_control(content)
    has_user_ban_control(content)
    has_user_posts_filter_control(content)


@pytest.mark.parametrize("user", ["regular", "root"])
def test_get_users(request, user):
    client = get_client(request, user)
    content = get_users(client)
    user = get_dynamodb_user(user_ids[user])
    assert user["name"] in content


@pytest.mark.parametrize("user", ["regular", "root"])
def test_get_contacts(request, user):
    client = get_client(request, user)
    resp = get(client, "/contacts")
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/any", "/any/any", "/missing", "/foo/bar"])
def test_not_found(guest_client, path):
    resp = get(guest_client, path)
    assert resp.status_code == 404


@pytest.mark.parametrize("user", ["regular", "root"])
def test_logout(request, user):
    client = get_client(request, user)
    resp = get(client, "/auth/logout")
    assert resp.status_code == 200
