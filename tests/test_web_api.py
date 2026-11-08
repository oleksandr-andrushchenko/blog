#!/usr/bin/env python3

import base64
import json
import os
import time
import uuid
from email import policy
from email.parser import BytesParser
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qsl, parse_qs, quote, urlencode, urlsplit

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


@pytest.mark.parametrize('method, path', [
    ('GET', '/users/legacy-user/integrations/medium'),
    ('POST', '/users/legacy-user/integrations/medium'),
    ('DELETE', '/users/legacy-user/integrations/medium'),
    ('POST', '/articles/legacy-article/share/medium'),
])
def test_removed_medium_endpoints_return_not_found(guest_client, method, path):
    response = guest_client.request(method, os.environ['API_TEST_BASE_URL'] + path, timeout=10)
    assert response.status_code == 404, response.text


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
    assert schema["@type"] == "ProfilePage"
    assert schema["mainEntity"]["@type"] == "Person"
    assert schema["mainEntity"]["@id"] == schema["mainEntity"]["url"] + "#person"
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
    assert header_el('a[href$="/categories"]').parent().next()('a[href$="/tags"]').text() == "Tags"
    assert not header_el('a[href$="/users"]')
    for path, label in (("/tags", "Tags"), ("/users", "Community")):
        assert doc("footer")(f'a[href$="{path}"]').text() == label
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
    assert bool(user_impressions_el) == (follow_control or block_control)
    metadata = main_el('.user-meta').eq(0)
    assert int(metadata('.followers-count').text().replace(',', '') or 0) == followers_count
    assert int(metadata('.following-count').text().replace(',', '') or 0) == following_count
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
    controls = doc("#user-controls")
    form = controls("#user-status-form")
    assert bool(form) == (activate_control or ban_control)
    if form:
        assert form('input[name="status"][value="active"]')
        assert form('input[name="status"][value="banned"]')
        assert form('textarea[name="comment"]')
        assert form.attr("action").startswith(os.environ["API_TEST_BASE_URL"])
    if controls("#user-report-form"):
        assert controls("#user-report-toggle").has_class("btn-light")
    if controls(".user-impressions"):
        assert controls(".user-impressions").has_class("d-inline-flex")
    assert not controls(".user-impressions").has_class("mb-2")
    message = controls('a[href*="/messages/"]')
    if message:
        assert message.has_class("btn-light")


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
    profile_articles = main_el("#my-articles")
    status_controls_el = profile_articles if profile_articles else (form_el if form_el else main_el)
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
        assert tags_el.attr("data-inject-hidden") == "true"
        assert tags_el.attr("data-auto-submit") == "true"
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


def test_contact_reasons_match_menu_and_prefill_messages(guest_client):
    doc = get_contacts(guest_client)
    menu_links = list(doc('ul[aria-labelledby="contact-menu-toggle"] a').items())
    reason_links = list(doc('section[aria-labelledby="contact-reasons-heading"] a').items())
    assert len(reason_links) == len(menu_links) == 5
    for reason, menu in zip(reason_links, menu_links):
        assert reason.text() == menu.text()
        assert reason('i').attr('class').split()[1] == menu('i').attr('class').split()[1]
        destination = urlsplit(reason.attr('href'))
        menu_destination = urlsplit(menu.attr('href'))
        assert (destination.path, destination.query) == (menu_destination.path, menu_destination.query)
        assert destination.fragment == 'contact-form'
        response = get(guest_client, reason.attr('href'))
        assert response.status_code == 200
        expected_message = parse_qs(destination.query).get('message', [''])[0]
        assert (pq(response.text)('#message')[0].text or '') == expected_message


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

    latest_link = doc("#latest-article-comments .border-top a").eq(0).attr("href")
    assert latest_link.startswith(f"/articles/{article_id}#comment-")

    dynamodb_table.update_item(
        Key={"pk": f"POST#{article_id}", "sk": "META"},
        UpdateExpression="SET title = :title",
        ExpressionAttributeValues={":title": "Updated discussion article title"},
    )
    updated = get_index(guest_client)("#latest-article-comments").text()
    assert "Updated discussion article title" in updated
    assert article_title not in updated

    try:
        for status in ("unpublished", "rejected"):
            dynamodb_table.update_item(
                Key={"pk": f"POST#{article_id}", "sk": "META"},
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":status": status},
            )
            hidden = get_index(guest_client)("#latest-article-comments").text()
            assert not any(text in hidden for text in comment_texts)
    finally:
        dynamodb_table.update_item(
            Key={"pk": f"POST#{article_id}", "sk": "META"},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "published"},
        )


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
    ("/earn-with-us", "Earn support", "h1"),
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
    "/articles?type=invalid",
    "/articles?status=invalid",
    "/latest/articles?limit=invalid",
    "/popular/articles?status=invalid",
    "/rest/articles?type=invalid",
    "/articles?category=Invalid category",
    "/articles-fragment?limit=0",
    "/users?type=invalid",
    "/latest/users?limit=invalid",
    "/popular/users?status=invalid",
    "/users-fragment?status=invalid",
    "/tags?prefix=",
    "/tags?type=invalid",
])
def test_public_query_endpoints_reject_invalid_parameters(guest_client, path):
    response = get(guest_client, path)
    assert response.status_code == 422, (path, response.status_code, response.text)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/rest/users", "/invalid/users", "/rest/users?limit=invalid"])
def test_unknown_user_listing_paths_return_not_found(guest_client, method, path):
    response = get(guest_client, path) if method == "GET" else guest_client.head(WEB_TEST_BASE_URL + path)
    assert response.status_code == 404, (path, response.status_code)


@pytest.mark.parametrize("path", [
    "/latest/articles", "/popular/articles", "/rest/articles",
    "/popular/rest/articles", "/rest/latest/articles",
    "/latest/users", "/popular/users",
])
def test_supported_listing_paths_and_article_tags_remain_valid(guest_client, path):
    response = get(guest_client, path)
    assert response.status_code == 200, (path, response.status_code, response.text)


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


@pytest.mark.parametrize("path", ["/login", "/login-callback?code=expired", "/logout", "/logout-callback"])
def test_login_does_not_return_to_authentication_pages(path):
    response = get(get_guest_client(), "/login", headers={"Referer": WEB_TEST_BASE_URL + path},
                   allow_redirects=False)
    assert response.status_code == 307
    assert SimpleCookie(response.headers["Set-Cookie"])["redirect_url"].value == "/"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("target, expected", [
    ("/login-callback?code=expired", "/"),
    ("/login-callback/", "/"),
    ("/%6cogin-callback?code=expired", "/"),
    ("/logout-callback", "/"),
    ("https://external.example/", "/"),
    ("//external.example/", "/"),
    ("/articles?limit=2", "/articles?limit=2"),
    ("/articles?offset=invalid&limit=2&offset=#comments", "/articles?limit=2#comments"),
])
def test_login_callback_validates_stored_return_destination(target, expected):
    from shared_utils import encode_offset

    code = encode_offset({"sub": "callback-return-sub", "iss": "callback-return-iss",
                          "email": "callback-return@example.com"})
    response = get(get_guest_client(), "/login-callback", params={"code": code},
                   headers={"Cookie": f"redirect_url={target}"}, allow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.headers["location"] == expected
    assert response.cookies.get("token")
    assert 'redirect_url=""' in response.headers["Set-Cookie"]
    assert "Max-Age=0" in response.headers["Set-Cookie"]
    assert response.headers["Cache-Control"] == "no-store"


def test_login_preserves_normal_return_destinations():
    response = get(get_guest_client(), "/login", headers={"Referer": WEB_TEST_BASE_URL + "/tags"},
                   allow_redirects=False)
    assert SimpleCookie(response.headers["Set-Cookie"])["redirect_url"].value == WEB_TEST_BASE_URL + "/tags"
    response = get(get_guest_client(), "/login", params={"redirect_url": "/articles"},
                   allow_redirects=False)
    assert SimpleCookie(response.headers["Set-Cookie"])["redirect_url"].value == "/articles"
    response = get(get_guest_client(), "/login", params={
        "redirect_url": "/articles?tags=cache&offset=invalid&tags=queues&limit=2#comments",
    }, allow_redirects=False)
    assert SimpleCookie(response.headers["Set-Cookie"])["redirect_url"].value == "/articles?tags=cache&tags=queues&limit=2#comments"


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
    edit_doc = pq(edit_success.text)
    form = edit_doc('#user-form')
    assert [tab.text() for tab in form('[role="tab"]').items()] == [
        'Personal info', 'Links', 'Earn', 'Notifications', 'Integrations',
    ]
    assert len(form('#notifications input[type="checkbox"]:checked')) == 8
    assert form('.tab-pane.active').attr('id') == 'personal-info'
    assert form('#personal-info [name="name"]') and form('#personal-info [name="avatar_action"]')
    assert form('#links [name="website"]') and form('#links [name="github_username"]')
    assert not form('[name="bmc_username"]')
    assert form('#earn #support-links')
    assert form('#integrations-tab-item').has_class('d-none')
    assert not form('[id$="-availability-error"]')
    assert 'Could not check available' not in edit_success.text
    assert form('#integrations #telegram-connect').text() == 'Connect Telegram'
    assert form('#integrations #linkedin-connect').text() == 'Connect LinkedIn'
    assert not form('#integrations input[name]')
    assert not form('#medium-integration')
    assert not form('#telegram-token')
    linkedin_url = f"/users/{root_id}/integrations/linkedin"
    linkedin_status = get(root_user_client, linkedin_url)
    assert linkedin_status.status_code == 200, linkedin_status.text
    assert linkedin_status.json() == {'configured': False, 'connected': False, 'pending': False}
    assert linkedin_status.headers['Cache-Control'] == 'no-store'
    assert get(guest_client, linkedin_url).status_code == 401
    assert post(root_user_client, linkedin_url, json={}).status_code == 503
    assert post(root_user_client, linkedin_url, json={},
                headers={'Origin': 'https://untrusted.example'}).status_code == 403
    connection_url = f"/users/{root_id}/integrations/telegram"
    connection = get(root_user_client, connection_url)
    assert connection.status_code == 200, connection.text
    assert connection.json() == {"connected": False, "configured": False, "pending": False}
    assert connection.headers['Cache-Control'] == 'no-store'
    assert get(guest_client, connection_url).status_code == 401
    assert post(root_user_client, connection_url, json={}).status_code == 503
    assert post(root_user_client, connection_url, json={},
                  headers={"Origin": "https://untrusted.example"}).status_code == 403
    webhook = guest_client.post(os.environ['API_TEST_BASE_URL'] + '/integrations/telegram/webhook', json={})
    assert webhook.status_code == 503
    assert delete(root_user_client, connection_url).status_code == 200
    other_id = get_dynamodb_user_by_email(regular_user["email"])["id"]
    assert get(root_user_client, f"/users/{other_id}/integrations/telegram").status_code == 403
    other_profile = get(root_user_client, f"/users/{other_id}/edit")
    assert other_profile.status_code == 200
    assert not pq(other_profile.text)('#integrations')

    assert len(form('[type="submit"]')) == 1
    assert not form('[type="submit"]').parents('.tab-pane')
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
    content_input = pq(new_success.text)('#content')
    assert content_input.attr('minlength') is None
    assert content_input.attr('maxlength') is None
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
    assert article_item["category_name"] == "Distributed Systems"
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
    assert schema["breadcrumb"]["itemListElement"][-1]["name"] == "For creators"
    assert doc('meta[name="description"]').attr("content").startswith("Publish articles")
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
    assert article_schema["@id"] == article_schema["url"] + "#article"
    assert article_schema["author"]["@id"] == article_schema["author"]["url"] + "#person"
    assert article_schema["mainEntityOfPage"]["@type"] == "WebPage"
    assert article_schema["mainEntityOfPage"]["breadcrumb"]["itemListElement"][-1]["name"] == article_schema["headline"]
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
    assert read_doc('meta[name="robots"]').attr("content") == "noindex, nofollow"
    assert article_schema["publisher"]["name"] == "SysDesPro"
    assert article_schema["publisher"]["@id"] != article_schema["author"]["@id"]
    id_response = get(root_client, f"/articles/{article_id}?source=audit", allow_redirects=False)
    assert id_response.status_code == 308
    assert id_response.headers["Location"].endswith(
        f"/@root-functional/{functional_state['article_slug']}?source=audit")
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
        ExpressionAttributeValues={":filename": "sysdespro_1200x630.png"},
    )
    image_doc = pq(get(root_client, f"/articles/{article_id}").text)
    image_schema = json.loads(image_doc('script[type="application/ld+json"]').text())
    assert image_schema["image"][0].endswith("/sysdespro_1200x630.png")
    assert image_schema["thumbnailUrl"].endswith("/sysdespro_1200x630.png")
    assert image_doc('meta[property="og:image"]').attr("content").endswith("/sysdespro_1200x630.png")
    dynamodb_table.update_item(
        Key={"pk": f"POST#{article_id}", "sk": "META"},
        UpdateExpression="REMOVE image_filename",
    )

    for client in (guest_client, regular_client):
        for path in (f"/articles/{article_id}", f"/@root-functional/{functional_state['article_slug']}"):
            response = get(client, path, allow_redirects=False)
            assert response.status_code == 404, response.text

    read_failure = get(guest_client, "/articles/missing-article")
    assert read_failure.status_code == 404

    edit_success = get(root_client, f"/articles/{article_id}/edit")
    assert edit_success.status_code == 200
    content_input = pq(edit_success.text)('#content')
    assert content_input.attr('minlength') is None
    assert content_input.attr('maxlength') is None
    edit_doc = pq(edit_success.text)
    raw_editor_content = edit_doc("textarea.editor").text()
    assert f'<img src="/{ARTICLE_IMAGE_FILENAME}" alt="{ARTICLE_IMAGE_ALT}">' in raw_editor_content
    assert "<picture>" not in raw_editor_content
    edit_failure = get(regular_client, f"/articles/{article_id}/edit")
    assert edit_failure.status_code == 404

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
    published_admin_doc = pq(get(root_client, f"/articles/{article_id}").text)
    assert len(published_admin_doc('.article-sharing')) == 1
    for platform in ('telegram', 'threads', 'linkedin'):
        button = published_admin_doc(f'button#{platform}-share')
        assert button.has_class('btn-light')
        assert button.attr('disabled') is not None
        assert button.attr('data-state') == 'unavailable'
        assert button.attr('data-share-url') is None
    assert not published_admin_doc('#medium-share')

    published_doc = pq(get(guest_client, f"/articles/{article_id}").text)
    assert published_doc('meta[name="robots"]').attr("content") == "index, follow"

    published_tag_page = get(guest_client, "/articles?type=latest&status=published&tags=functional-tag")
    assert published_tag_page.status_code == 200
    assert "Updated functional endpoint coverage article" in pq(published_tag_page.text)("#articles").text()

    published_admin_doc = pq(get(root_client, f"/articles/{article_id}").text)
    assert published_admin_doc('#article-status-form input[name="status"][value="unpublished"]')
    assert published_admin_doc('#article-status-form input[name="status"][value="published"][checked]')
    assert not published_doc('#article-status-form')
    report = published_doc('#article-report-form')
    assert report.attr('action') == os.environ['API_TEST_BASE_URL'] + f'/articles/{article_id}/report'
    assert report('select[name="reason"] option[value="spam"]')
    assert report('input[name="name"]').attr('value') == ''
    assert report('input[name="email"]').attr('value') == ''
    assert not published_admin_doc('.btn-article-publish, .btn-article-unpublish, .btn-article-reject')
    before_owner = get_dynamodb_user_by_email(root_user["email"])
    before_article = get_dynamodb_article(article_id)
    before_category = dynamodb_table.get_item(Key={"pk": "CATEGORY", "sk": "distributed-systems"})["Item"]

    # Repeating publication must not inflate counters.
    repeat = post(root_client, f"/articles/{article_id}/status", json={"status": "published"})
    assert repeat.status_code == 200, repeat.text
    assert get_dynamodb_user_by_email(root_user["email"])["published_posts_count"] == before_owner["published_posts_count"]
    denied = post(regular_client, f"/articles/{article_id}/status", json={"status": "unpublished"})
    assert denied.status_code == 403

    unpublished = post(root_client, f"/articles/{article_id}/status", json={"status": "unpublished"})
    assert unpublished.status_code == 200, unpublished.text
    unpublished_item = get_dynamodb_article(article_id)
    assert unpublished_item["status"] == "unpublished"
    assert unpublished_item["published_at"] == before_article["published_at"]
    assert unpublished_item["content"] == before_article["content"]
    owner = get_dynamodb_user_by_email(root_user["email"])
    assert owner["published_posts_count"] == before_owner["published_posts_count"] - 1
    assert owner["unpublished_posts_count"] == before_owner["unpublished_posts_count"] + 1
    category = dynamodb_table.get_item(Key={"pk": "CATEGORY", "sk": "distributed-systems"})["Item"]
    assert category["published_articles_count"] == before_category["published_articles_count"] - 1
    hidden_tag_page = get(guest_client, "/articles?tags=functional-tag")
    assert "Updated functional endpoint coverage article" not in pq(hidden_tag_page.text)("#articles").text()
    for path in (f"/articles/{article_id}", f"/@root-functional/{functional_state['article_slug']}"):
        assert get(guest_client, path, allow_redirects=False).status_code == 404
    assert get(root_client, f"/articles/{article_id}").status_code == 200

    republished = post(root_client, f"/articles/{article_id}/status", json={"status": "published"})
    assert republished.status_code == 200, republished.text
    assert get_dynamodb_user_by_email(root_user["email"])["published_posts_count"] == before_owner["published_posts_count"]
    assert get_dynamodb_article(article_id)["published_at"] == before_article["published_at"]
    assert get(guest_client, f"/articles/{article_id}").status_code == 200

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


@pytest.mark.parametrize("user_alias", ["regular", "root", None])
def test_article_image_upload_checks_auth_and_saves_resized_image(guest_client, user_alias):
    from io import BytesIO

    from PIL import Image

    original = BytesIO()
    Image.new("RGB", (1600, 800), "red").save(original, format="PNG")
    client = get_logged_in_client({"regular": regular_user, "root": root_user}[user_alias]) if user_alias else guest_client
    response = post(client, "/article-images", files={
        "file": ("article-cover.png", original.getvalue(), "image/png"),
    })
    if user_alias is None:
        assert response.status_code == 401, response.text
        return
    assert response.status_code == 200, response.text
    assert response.json().endswith("_1200x600.png")
    uploaded_path = Path("/app/static") / response.json()
    try:
        with Image.open(uploaded_path) as uploaded:
            assert uploaded.size == (1200, 600)
    finally:
        uploaded_path.unlink()


def test_article_image_upload_rejects_banned_account():
    from io import BytesIO

    from PIL import Image

    original = BytesIO()
    Image.new("RGB", (800, 400), "red").save(original, format="PNG")
    client = get_logged_in_client(regular_user)
    user_id = user_ids["regular"]
    previous_status = get_dynamodb_user(user_id)["status"]
    key = {"pk": f"USER#{user_id}", "sk": "META"}
    try:
        dynamodb_table.update_item(
            Key=key, UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "banned"},
        )
        response = post(client, "/article-images", files={
            "file": ("article-cover.png", original.getvalue(), "image/png"),
        })
        assert response.status_code == 403, response.text
    finally:
        dynamodb_table.update_item(
            Key=key, UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": previous_status},
        )


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
    assert sitemap_success.status_code == 202, sitemap_success.text
    assert sitemap_success.json()["status"] == "accepted"
    assert sitemap_success.json()["sitemap_url"] == f"{os.getenv('WEB_TEST_BASE_URL')}/sitemap.xml"
    for _ in range(100):
        sitemap = get(guest_client, "/sitemap.xml")
        if sitemap.status_code == 200 and "/functional-tag/articles" in sitemap.text:
            break
        time.sleep(0.1)
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
    messages = {}
    for email_file in new_emails:
        with email_file.open("rb") as stream:
            message = BytesParser(policy=policy.default).parse(stream)
        if message['Subject'].startswith('New article matching your interests:'):
            messages[message["To"]] = message

    assert len(messages) == 2
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
        response = get(guest_client, f"{legacy_path}?limit=5&source=legacy", allow_redirects=False)
        assert response.status_code == 308
        assert response.headers["Location"].endswith(f"{canonical_path}?limit=5&source=legacy")

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


@pytest.mark.parametrize("path", ["/articles", "/users", "/tags", "/@root-functional"])
def test_collection_pagination_uses_api_buttons_without_cursor_links(guest_client, path):
    response = guest_client.get(f"{WEB_TEST_BASE_URL}{path}?limit=1", timeout=30)
    assert response.status_code == 200, response.text
    doc = pq(response.text)
    button = doc("button.btn-load-more")
    assert len(button) == 1
    assert button.attr("type") == "button"
    assert not button.attr("href")
    assert not doc('a[rel="next"], a[rel="prev"]')
    assert not any("offset=" in (link.attr("href") or "") for link in doc("a[href], link[href]").items())
    endpoint = urlsplit(button.attr("data-url"))
    assert endpoint.netloc == urlsplit(os.environ["API_TEST_BASE_URL"]).netloc
    query = parse_qs(endpoint.query)
    query.update({"offset": [button.attr("data-offset")], "limit": ["1"]})
    fragment = get(guest_client, endpoint.path + "?" + urlencode(query, doseq=True))
    assert fragment.status_code == 200, fragment.text
    assert pq(fragment.text)(".article, .user, .tag")
    # Existing web pagination URLs redirect to the first page; only API fragments paginate.
    next_page = guest_client.get(
        f"{WEB_TEST_BASE_URL}{path}?" + urlencode({"limit": 1, "offset": button.attr("data-offset")}),
        timeout=30, allow_redirects=False,
    )
    assert next_page.status_code == 308, next_page.text
    destination = urlsplit(next_page.headers["Location"])
    assert destination.path == path
    assert parse_qs(destination.query) == {"limit": ["1"]}
    next_page = guest_client.get(next_page.headers["Location"], timeout=30)
    assert next_page.status_code == 200, next_page.text
    next_doc = pq(next_page.text)
    assert not any("offset=" in (link.attr("href") or "") for link in next_doc("a[href], link[href]").items())
    assert "offset=" not in (next_doc('meta[property="og:url"]').attr("content") or "")


def test_search_and_empty_archives_are_not_indexable(guest_client):
    response = guest_client.get(f"{WEB_TEST_BASE_URL}/tags?prefix=cache&limit=1", timeout=30)
    assert response.status_code == 200
    search = pq(response.text)
    assert search('meta[name="robots"]').attr("content") == "noindex, follow"
    assert parse_qs(urlsplit(search('link[rel="canonical"]').attr("href")).query) == {
        "prefix": ["cache"], "limit": ["1"]}
    empty = pq(get(guest_client, "/seo-audit-nonexistent-tag/articles").text)
    assert empty('meta[name="robots"]').attr("content") == "noindex, follow"
    missing_category = get(guest_client, "/articles?category=seo-audit-nonexistent")
    assert missing_category.status_code == 200
    missing_doc = pq(missing_category.text)
    assert missing_doc("main h1")
    assert "Seo-Audit-Nonexistent" in missing_doc("main h1").text()
    assert not missing_doc("#articles .article")
    assert missing_doc('meta[name="robots"]').attr("content") == "noindex, follow"


def test_profile_id_redirect_preserves_query(guest_client):
    user_id = functional_state["root_id"]
    response = get(guest_client, f"/users/{user_id}?limit=1", allow_redirects=False)
    assert response.status_code == 308
    assert response.headers["Location"] == "/@root-functional?limit=1"


def test_discovery_tag_suggestions_skip_empty_pages_and_keep_editor_tags(guest_client):
    slugs = ["zz-discovery-a", "zz-discovery-b", "zz-discovery-c"]
    keys = []
    try:
        for slug, count in zip(slugs, [0, 0, 2]):
            key = {"pk": f"POST_TAG#{slug}", "sk": "META"}
            keys.append(key)
            dynamodb_table.put_item(Item=key | {
                "name": slug, "tag_type_pk": "POST_TAG", "tag_name_sk": slug,
                "created_at": 1, "rating_sk": 1, "posts_count": count})
        response = get(guest_client, "/tags?prefix=zz-discovery&limit=1&published_only=true")
        assert response.status_code == 200, response.text
        assert [tag["slug"] for tag in response.json()] == [slugs[2]]
        assert response.json()[0]["articles_count"] == 2
        unfiltered = get(guest_client, "/tags?prefix=zz-discovery&limit=1")
        assert unfiltered.status_code == 200, unfiltered.text
        assert unfiltered.json()[0]["slug"] == slugs[0]
        assert unfiltered.json()[0]["articles_count"] == 0
        assert get(guest_client, "/tags?prefix=zz-discovery-missing&limit=1&published_only=true").json() == []
        listing = pq(get(guest_client, "/articles").text)
        assert parse_qs(urlsplit(listing("#tags-input").attr("data-url")).query)["published_only"] == ["1"]
    finally:
        for key in keys:
            dynamodb_table.delete_item(Key=key)


def test_production_web_pagination_url_redirects_without_validating_cursor(guest_client):
    offset = ("eyJwb3N0X2NhdGVnb3J5X3N0YXR1c19wayI6ICJQT1NUI2Nsb3VkLWluZnJh"
              "c3RydWN0dXJlI3B1Ymxpc2hlZCIsICJzayI6ICJNRVRBIiwgInBrIjogIlBPU1Qj"
              "OTJhZTQ3NzUtNWM0ZS00NTFkLWI3MTMtYjRmYzNjMDQ2MmIwIiwgImNyZWF0ZWRf"
              "YXQiOiAxNzgyMzYwNDcwNDMzfQ==")
    response = get(guest_client, f"/popular/articles?category=cloud-infrastructure&offset={offset}", allow_redirects=False)
    assert response.status_code == 308
    destination = urlsplit(response.headers["Location"])
    assert destination.path == "/popular/articles"
    assert parse_qs(destination.query) == {"category": ["cloud-infrastructure"]}


def test_external_site_links_open_in_new_tabs(guest_client):
    for path in ("/privacy-policy", "/contacts", "/@root-functional"):
        response = get(guest_client, path)
        assert response.status_code == 200
        doc = pq(response.text)
        for link in doc('a[href^="https://"], a[href^="http://"]').items():
            if urlsplit(link.attr("href")).netloc == urlsplit(WEB_TEST_BASE_URL).netloc:
                continue
            assert link.attr("target") == "_blank"
            assert {"nofollow", "noopener", "noreferrer"} <= set((link.attr("rel") or "").split())


def test_related_articles_autoload_pages_are_public_unique_and_source_scoped(guest_client):
    client = get_logged_in_client(root_user)
    owner_id = get_logged_in_user_id(root_user)
    set_dynamodb_user_permissions(owner_id, ['root'])
    tag = 'related-' + uuid.uuid4().hex[:8]

    def create(number, tags, published=True):
        response = post(client, '/articles', json={
            'title': f'Related {tag} {number}', 'content': 'Related pagination regression. ' * 200,
            'category': 'distributed-systems', 'tags': tags,
        })
        assert response.status_code == 200, response.text
        path = urlsplit(response.json()).path
        identifier = path.rsplit('/', 1)[-1]
        article_id = identifier if path.startswith('/articles/') else dynamodb_table.get_item(Key={
            'pk': f'POST_SLUG#{identifier}', 'sk': 'META',
        })['Item']['post_id']
        if published:
            response = post(client, f'/articles/{article_id}/status', json={'status': 'published'})
            assert response.status_code == 200, response.text
        return article_id, path

    expected = {create(number, [tag, tag + '-second'])[1] for number in range(12)}
    private_id, private_path = create('private', [tag, tag + '-second'], published=False)
    _, unrelated_path = create('unrelated', [tag + '-unrelated'])
    source_id, source_path = create('source', [tag, tag + '-second'])
    page = get(guest_client, f'/articles/{source_id}')
    assert page.status_code == 200, page.text
    doc = pq(page.text)
    cards = doc('#related-articles > .article')
    assert len(cards) == 10
    seen = [urlsplit(card('.card-title').parent().attr('href')).path for card in cards.items()]
    button = doc('.btn-load-more[data-container="#related-articles"]')
    assert len(button) == 1 and button.attr('data-auto-click') is not None
    assert urlsplit(button.attr('data-url')).netloc == urlsplit(os.environ['API_TEST_BASE_URL']).netloc
    endpoint = urlsplit(button.attr('data-url')).path
    offset = button.attr('data-offset')
    first_offset = offset
    for _ in range(5):
        response = get(guest_client, endpoint + '?' + urlencode({'offset': offset, 'limit': 10}))
        assert response.status_code == 200, response.text
        new_cards = pq(response.text)('.article')
        seen.extend(urlsplit(card('.card-title').parent().attr('href')).path for card in new_cards.items())
        offset = new_cards.eq(-1).attr('data-offset') if new_cards else None
        if not offset:
            break
    assert not offset
    assert len(seen) == len(set(seen)) == 12
    assert set(seen) == expected
    assert {private_path, unrelated_path, source_path}.isdisjoint(seen)
    for params in ({'offset': 'invalid'}, {'offset': first_offset, 'limit': 0}, {'limit': 1000}):
        invalid = get(guest_client, endpoint + '?' + urlencode(params))
        assert invalid.status_code == 422, invalid.text
    other_id = create('other-source', [tag, tag + '-second'])[0]
    wrong_source = get(guest_client, f'/articles/{other_id}/related-fragment?' + urlencode({'offset': first_offset}))
    assert wrong_source.status_code == 422, wrong_source.text
    private = get(guest_client, f'/articles/{private_id}/related-fragment')
    assert private.status_code == 404, private.text


def test_guest_article_controls_preserve_return_url_and_keep_support_public(guest_client):
    root_client = get_logged_in_client(root_user)
    article_id = functional_state["article_id"]
    article_item = get_dynamodb_article(article_id)
    owner_id = article_item["user_id"]
    owner = get_dynamodb_user(owner_id)
    original = owner.get("bmc_username")
    dynamodb_table.update_item(
        Key={"pk": f"USER#{owner_id}", "sk": "META"},
        UpdateExpression="SET bmc_username = :value",
        ExpressionAttributeValues={":value": "controls-author"},
    )
    try:
        response = get(guest_client, f"/articles/{article_id}?source=controls&ref=guest")
        assert response.status_code == 200, response.text
        doc = pq(response.text)
        assert len(doc("#article-controls .article-impressions")) == 1
        assert len(doc("#article-controls .share")) == 1
        assert not doc("#article-controls .btn-article-like, #article-controls .btn-article-dislike")
        canonical = urlsplit(response.url)
        destination = canonical.path + "?" + canonical.query
        for label in ("Sign in to like this article", "Sign in to dislike this article"):
            link = doc(f'#article-controls a[aria-label="{label}"]')
            assert len(link) == 1
            login = urlsplit(link.attr("href"))
            assert login.path == "/login"
            assert parse_qs(login.query)["redirect_url"] == [destination]
            login_response = get(guest_client, link.attr("href"), allow_redirects=False)
            assert SimpleCookie(login_response.headers["Set-Cookie"])["redirect_url"].value == destination
        support_section = doc("aside > .creator-support-link")
        assert len(doc(".creator-support-link")) == len(support_section) == 1
        assert support_section.prev("section")("h2").text() == "Author"
        assert not doc("#article-controls .creator-support-link")
        support = support_section("form")
        assert len(support) == 1
        assert support.attr("action").endswith(f"/articles/{article_id}/support/buy-me-a-coffee")
        assert set(support.attr("rel").split()) == {"nofollow", "noopener"}
        signed_in = pq(get(root_client, destination).text)
        assert len(signed_in("#article-controls .article-impressions .btn-article-like")) == 1
        assert len(signed_in("#article-controls #article-status-comment")) == 1
        edit_link = signed_in(f'#article-controls a[href$="/articles/{article_id}/edit"]')
        assert len(edit_link) == 1
        assert edit_link.text() == 'Edit article'
        assert len(signed_in('aside > .creator-support-link')) == 1
        assert not signed_in('#article-controls .creator-support-link')
    finally:
        dynamodb_table.update_item(
            Key={"pk": f"USER#{owner_id}", "sk": "META"},
            UpdateExpression="SET bmc_username = :value",
            ExpressionAttributeValues={":value": original},
        )


@pytest.mark.parametrize("path", ["/tags", "/articles-fragment", "/users-fragment", "/tags-fragment"])
@pytest.mark.parametrize("offset", ["756028461", "invalid", "e30=", "W10=", "bnVsbA=="])
def test_collection_routes_reject_malformed_pagination_offsets(guest_client, path, offset):
    response = get(guest_client, path + "?" + urlencode({"offset": offset}))
    assert response.status_code == 422, (path, response.status_code, response.text)


def test_profile_and_comment_offsets_are_bound_to_their_collection(guest_client):
    from shared_utils import encode_offset
    wrong_profile = encode_offset({"pk": "POST#example", "sk": "META", "post_user_status_pk": "POST#another-user#published", "created_at": 1})
    global_feed = encode_offset({"pk": "POST#example", "sk": "META", "post_status_pk": "POST#published", "created_at": 1})
    for offset in (wrong_profile, global_feed):
        for path in (f"/users/{user_ids['root']}/articles-fragment",):
            response = get(guest_client, path + "?" + urlencode({"offset": offset}))
            assert response.status_code == 422, response.text
    article_id = functional_state["article_id"]
    offset = encode_offset({"pk": "POST#another-article", "sk": "COMMENT#1#example"})
    response = get(guest_client, f"/articles/{article_id}/comments-fragment?" + urlencode({"offset": offset}))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("path", [
    "/", "/articles", "/latest/cache/articles", "/users", "/tags", "/@root-functional",
    "/root-functional", "/posts", "/post-tags", "/login", "/articles/", "/missing-page",
])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_web_offsets_redirect_before_page_rendering_or_legacy_redirects(guest_client, path, method):
    response = guest_client.request(
        method, f"{WEB_TEST_BASE_URL}{path}",
        params=[("tags", "cache"), ("offset", "756028461"), ("tags", "queues"),
                ("limit", "5"), ("offset", "")],
        allow_redirects=False, timeout=30,
    )
    assert response.status_code == 308, response.text
    destination = urlsplit(response.headers["Location"])
    assert destination.path == path
    assert parse_qs(destination.query) == {"tags": ["cache", "queues"], "limit": ["5"]}
    assert "offset" not in response.headers["Location"]
    if path == "/login":
        assert response.headers["Cache-Control"] == "no-store"


def test_email_notification_preferences_and_manual_actions_end_to_end(guest_client):
    suffix = uuid.uuid4().hex
    owner_data = {'sub': f'notification-owner-{suffix}', 'iss': 'example-provider',
                  'email': f'notification-owner-{suffix}@example.com', 'name': 'Notification Creator'}
    actor_data = {'sub': f'notification-actor-{suffix}', 'iss': 'example-provider',
                  'email': f'notification-actor-{suffix}@example.com', 'name': 'Notification Actor'}
    owner = get_logged_in_client(owner_data)
    actor = get_logged_in_client(actor_data)
    admin = get_logged_in_client(root_user)
    owner_id = get_dynamodb_user_by_email(owner_data['email'])['id']
    user_path = f'/users/{owner_id}'
    preferences = ('notify_article_status', 'notify_user_impressions', 'notify_article_impressions', 'notify_tag_articles')
    directory = Path('/app/.emails')
    seen = set(directory.glob('*.eml'))

    def emails():
        found = []
        for path in set(directory.glob('*.eml')) - seen:
            seen.add(path)
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            if message['To'] == owner_data['email']:
                found.append(message)
        return found

    form = pq(get(owner, user_path + '/edit').text)
    for field in preferences:
        assert form(f'#notifications input[name="{field}"]:checked')
    assert patch(guest_client, user_path, json={'notify_article_status': False}).status_code == 401
    assert patch(actor, user_path, json={'notify_article_status': False}).status_code == 403
    for invalid in (None, 'false', 0):
        assert patch(owner, user_path, json={'notify_article_status': invalid}).status_code == 422

    title = f'Notification coverage {suffix}'
    created = post(owner, '/articles', json={
        'title': title, 'content': ARTICLE_CONTENT,
        'category': 'distributed-systems', 'tags': ['notification-coverage'],
    })
    assert created.status_code == 200, created.text
    item = next(item for item in dynamodb_table.scan()['Items'] if item.get('title') == title)
    article_path = f'/articles/{item["id"]}'
    assert not emails()
    for status in ('published', 'unpublished', 'rejected'):
        values = {'status': status, **({'comment': 'Please revise <the example>.'} if status == 'rejected' else {})}
        response = post(admin, article_path + '/status', json=values)
        assert response.status_code == 200, response.text
        messages = emails()
        assert len(messages) == 1
        assert messages[0]['Subject'] == 'Your article status changed'
        assert status in messages[0].get_body('plain').get_content()
        assert '#notifications' in messages[0].get_body('html').get_content()
        if status == 'rejected':
            assert '&lt;the example&gt;' in messages[0].get_body('html').get_content()
        assert post(admin, article_path + '/status', json=values).status_code == 200
        assert not emails()

    assert post(admin, article_path + '/status', json={'status': 'published'}).status_code == 200
    emails()
    for action, expected_count in (('like', 1), ('dislike', 0), ('dislike', 0), ('dislike', 1)):
        response = post(actor, article_path + '/impression', json={'action': action})
        assert response.status_code == 200, response.text
        messages = emails()
        assert len(messages) == expected_count
        if messages:
            assert messages[0]['Subject'] == 'New reaction to your article'
            assert 'Notification Actor' in messages[0].get_body('plain').get_content()
    for action, expected_count in (('follow', 1), ('block', 0), ('block', 0), ('block', 1)):
        response = post(actor, user_path + '/impression', json={'action': action})
        assert response.status_code == 200, response.text
        assert len(emails()) == expected_count

    response = patch(owner, user_path, json={field: False for field in preferences})
    assert response.status_code == 200, response.text
    assert patch(owner, user_path, json={'headline': 'Updated creator profile'}).status_code == 200
    saved = get_dynamodb_user(owner_id)
    form = pq(get(owner, user_path + '/edit').text)
    for field in preferences:
        assert saved[field] is False
        assert not form(f'#notifications input[name="{field}"]:checked')
    assert post(admin, article_path + '/status', json={'status': 'unpublished'}).status_code == 200
    assert post(admin, article_path + '/status', json={'status': 'published'}).status_code == 200
    # Remove existing reactions, then create new ones with email preferences disabled.
    for path, action in ((user_path + '/impression', 'block'), (article_path + '/impression', 'dislike')):
        assert post(actor, path, json={'action': action}).status_code == 200
        assert post(actor, path, json={'action': action}).status_code == 200
    assert not emails()

    tag = f'notification-{suffix[:12]}'
    assert post(owner, '/tag-subscriptions', json={'tags': [tag]}).status_code == 200
    for enabled in (False, True):
        assert patch(owner, user_path, json={'notify_tag_articles': enabled}).status_code == 200
        tagged_title = f'Interest email {enabled} {suffix}'
        created = post(actor, '/articles', json={
            'title': tagged_title, 'content': ARTICLE_CONTENT,
            'category': 'distributed-systems', 'tags': [tag],
        })
        assert created.status_code == 200, created.text
        tagged = next(item for item in dynamodb_table.scan()['Items'] if item.get('title') == tagged_title)
        assert post(admin, f'/articles/{tagged["id"]}/status', json={'status': 'published'}).status_code == 200
        messages = emails()
        assert len(messages) == int(enabled)
        if enabled:
            assert messages[0]['Subject'].startswith('New article matching your interests:')


def test_support_links_roundtrip_public_navigation_and_owner_statistics(guest_client):
    admin = get_logged_in_client(root_user)
    user_id = get_dynamodb_user_by_email('root@example.com')['id']
    path = f'/users/{user_id}'
    links = [{'id': 'membership', 'name': 'Membership', 'text': 'Become a member',
              'url': 'https://support.example/creator'}]
    response = patch(admin, path, json={'support_links': links})
    assert response.status_code == 200, response.text
    try:
        editor = get(admin, path + '/edit')
        assert editor.status_code == 200, editor.text
        assert pq(editor.text)('[data-support-link] [data-link-field="name"]').attr('value') == 'Membership'
        profile = get(guest_client, path)
        assert profile.status_code == 200, profile.text
        action = f'/users/{user_id}/support/membership'
        assert pq(profile.text)(f'form[action="{os.getenv("WEB_TEST_BASE_URL")}{action}"]')
        # Crawlers cannot trigger support navigation with GET.
        assert get(guest_client, action).status_code == 405
        external = guest_client.post(os.getenv('WEB_TEST_BASE_URL') + action,
                                     headers={'Origin': 'https://outside.example'})
        assert external.status_code == 403
        opened = guest_client.post(os.getenv('WEB_TEST_BASE_URL') + action,
                                   headers={'Origin': os.getenv('WEB_TEST_BASE_URL')}, allow_redirects=False)
        assert opened.status_code == 303, opened.text
        assert opened.headers['X-Robots-Tag'] == 'noindex, nofollow'
        assert opened.headers['Location'] == 'https://support.example/creator'
        assert not opened.content
        assert opened.headers['Cache-Control'] == 'no-store'
        assert opened.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
        for _ in range(50):
            saved = get_dynamodb_user(user_id)
            if saved['support_links'][0]['clicks_count'] == 1:
                break
            time.sleep(0.1)
        assert saved['support_links'][0]['clicks_count'] == 1
        # Editing with stale client input must retain the server-owned count.
        assert patch(admin, path, json={'support_links': links}).status_code == 200
        assert get_dynamodb_user(user_id)['support_links'][0]['clicks_count'] == 1
    finally:
        assert patch(admin, path, json={'support_links': []}).status_code == 200


def test_feedback_contact_prefill_is_escaped_and_bounded(guest_client):
    article_id = functional_state['article_id']
    page = get(guest_client, f'/articles/{article_id}')
    assert page.status_code == 200, page.text
    payload = '</textarea><script>window.unsafe = true</script>'
    response = get(guest_client, '/contacts?' + urlencode({'message': payload}))
    doc = pq(response.text)
    assert doc('#message').text() == payload
    assert not doc('script').filter(lambda i, el: 'window.unsafe = true' == pq(el).text())
    assert doc('link[rel="canonical"]').attr('href') == WEB_TEST_BASE_URL + '/contacts'
    long_prefill = get(guest_client, '/contacts?' + urlencode({'message': 'x' * 1500}))
    assert len(pq(long_prefill.text)('#message').text()) == 1000
    assert 'Suggest an improvement' in page.text


def test_engagement_endpoints_and_notifications_end_to_end(guest_client):
    suffix = uuid.uuid4().hex
    owner_data = {'sub': 'engagement-owner-' + suffix, 'iss': 'example-provider',
                  'email': f'engagement-owner-{suffix}@example.com', 'name': 'Engagement Owner'}
    actor_data = {'sub': 'engagement-actor-' + suffix, 'iss': 'example-provider',
                  'email': f'engagement-actor-{suffix}@example.com', 'name': 'Engagement Reader'}
    owner, actor = get_logged_in_client(owner_data), get_logged_in_client(actor_data)
    admin = get_logged_in_client(root_user)
    owner_id = get_dynamodb_user_by_email(owner_data['email'])['id']
    actor_id = get_dynamodb_user_by_email(actor_data['email'])['id']
    directory = Path('/app/.emails')
    seen = set(directory.glob('*.eml'))
    title = 'Engagement coverage ' + suffix
    created = post(owner, '/articles', json={'title': title, 'content': ARTICLE_CONTENT,
                                            'tags': ['engagement'], 'category': 'distributed-systems'})
    assert created.status_code == 200, created.text
    item = next(item for item in dynamodb_table.scan()['Items'] if item.get('title') == title)
    path = '/articles/' + item['id']
    assert post(admin, path + '/status', json={'status': 'published'}).status_code == 200
    page = get(guest_client, path)
    assert page.status_code == 200, page.text
    page_doc = pq(page.text)
    share_toggle = page_doc('#article-controls #article-share-toggle')
    assert share_toggle.text() == 'Share'
    assert share_toggle.attr('data-bs-toggle') == 'dropdown'
    assert share_toggle.attr('aria-controls') == 'article-share-options'
    share_menu = page_doc('#article-share-options.dropdown-menu[data-share-url]')
    assert share_menu.attr('aria-labelledby') == 'article-share-toggle'
    share_controls = share_menu('[data-share-platform]')
    platforms = ['whatsapp', 'telegram', 'twitter', 'bluesky', 'threads',
                 'facebook', 'linkedin', 'reddit', 'email']
    assert [control.attr('data-share-platform') for control in share_controls.items()] == platforms
    for control in share_controls.items():
        assert control.attr('title') == control.attr('aria-label')
        assert control.text()
        assert control.has_class('dropdown-item')
        assert control('i[aria-hidden="true"]')
        assert set(control.attr('rel').split()) == {'nofollow', 'noopener', 'noreferrer'}
        if control.attr('data-share-platform') != 'email':
            assert control.attr('target') == '_blank'
    for platform in ['whatsapp', 'telegram', 'bluesky', 'threads', 'reddit']:
        event = {'channel': platform + '_share_click', 'event_id': str(uuid.uuid4())}
        assert post(guest_client, path + '/share-click', json=event).json() == {'recorded': True}
        assert post(guest_client, path + '/share-click', json=event).json() == {'recorded': False}
        assert get_dynamodb_article(item['id'])['shares_counts'][platform + '_share_click'] == 1
    seen |= set(directory.glob('*.eml'))
    comment_response = post(actor, path + '/comments', json={'text': 'A useful discussion.'})
    assert comment_response.status_code == 200, comment_response.text
    records = dynamodb_table.query(KeyConditionExpression='pk = :pk',
                                   ExpressionAttributeValues={':pk': 'POST#' + item['id']})['Items']
    comment = next(record for record in records if record['sk'].startswith('COMMENT#'))
    reaction_path = path + '/comments/' + quote(comment['id'], safe='') + '/impression'
    assert post(guest_client, reaction_path, json={'action': 'like'}).status_code == 401
    for action, likes, dislikes in [('like', 1, 0), ('dislike', 0, 1), ('dislike', 0, 0)]:
        result = post(owner, reaction_path, json={'action': action})
        assert result.status_code == 200, result.text
        assert result.json() == {'likes_count': likes, 'dislikes_count': dislikes}
    assert get_dynamodb_user(owner_id)['article_comments_received_count'] == 1
    assert get_dynamodb_user(actor_id)['article_comments_given_count'] == 1
    event = {'channel': 'email_share_click', 'event_id': str(uuid.uuid4())}
    assert post(guest_client, path + '/share-click', json=event).json() == {'recorded': True}
    assert post(guest_client, path + '/share-click', json=event).json() == {'recorded': False}
    assert post(guest_client, path + '/share-click', json={'channel': 'threads_publish', 'event_id': str(uuid.uuid4())}).status_code == 422
    assert get_dynamodb_user(owner_id)['article_shares_received_counts']['email_share_click'] == 1
    own_profile = pq(get(owner, '/users/' + owner_id).text)
    assert own_profile('#engagement-heading button').attr('aria-expanded') == 'false'
    assert own_profile('#community-activity').hasClass('collapse')
    assert not own_profile('#community-activity').hasClass('show')
    assert not pq(get(guest_client, '/users/' + owner_id).text)('#engagement-heading')
    messages = [BytesParser(policy=policy.default).parsebytes(file.read_bytes()) for file in set(directory.glob('*.eml')) - seen]
    assert any(message['To'] == owner_data['email'] and message['Subject'] == 'New comment on your article' for message in messages)
    assert any(message['To'] == owner_data['email'] and message['Subject'] == 'Your article was shared' for message in messages)
    assert patch(owner, '/users/' + owner_id, json={'notify_article_comments': False}).status_code == 200
    before = set(directory.glob('*.eml'))
    assert post(actor, path + '/comments', json={'text': 'Another comment.'}).status_code == 200
    assert not set(directory.glob('*.eml')) - before


@pytest.mark.parametrize('username', [None, 'chosen'])
def test_display_name_and_username_can_be_edited_independently(guest_client, username):
    suffix = uuid.uuid4().hex
    identity = {'sub': f'name-edit-{suffix}', 'iss': 'example-provider',
                'email': f'name-edit-{suffix}@example.com', 'name': 'Original Display Name'}
    client = get_logged_in_client(identity)
    user = get_dynamodb_user_by_email(identity['email'])
    user_path = f'/users/{user["id"]}'
    username = f'creator-{suffix[:12]}' if username else None
    response = patch(client, user_path, json={'username': username})
    assert response.status_code == 200, response.text
    expected_url = f'/@{username}' if username else user_path
    lock_key = {'pk': f'USER_SLUG#{username}', 'sk': 'META'}
    original_lock = dynamodb_table.get_item(Key=lock_key).get('Item') if username else None

    response = patch(client, user_path, json={'name': 'Updated Display Name'})
    assert response.status_code == 200, response.text
    assert response.json() == expected_url
    saved = get_dynamodb_user(user['id'])
    assert saved['name'] == 'Updated Display Name'
    assert saved.get('username') == username
    if username:
        assert dynamodb_table.get_item(Key=lock_key).get('Item') == original_lock
        assert 'Item' not in dynamodb_table.get_item(Key={'pk': f'USER_REDIRECT#{username}', 'sk': 'META'})
    assert pq(get(guest_client, expected_url).text)('h1').text().startswith('Updated Display Name')

    new_username = f'writer-{suffix[:12]}'
    response = patch(client, user_path, json={'username': new_username})
    assert response.status_code == 200, response.text
    assert response.json() == f'/@{new_username}'
    saved = get_dynamodb_user(user['id'])
    assert saved['name'] == 'Updated Display Name'
    assert saved['username'] == new_username


@pytest.mark.parametrize('email', [None, 'same-email'])
def test_facebook_sessions_keep_provider_account_isolated_even_without_email(email):
    suffix = uuid.uuid4().hex
    shared_email = f'facebook-identity-{suffix}@example.com'
    issuer = 'facebook-identity-tests'
    google = get_logged_in_client({'sub': 'google-' + suffix, 'iss': issuer, 'email': shared_email,
                                   'name': 'Existing Google Creator'})
    original = get_dynamodb_user_by_email(shared_email)
    facebook_sub = 'facebook-' + suffix
    facebook = get_logged_in_client({
        'sub': facebook_sub, 'iss': issuer, 'email': shared_email if email else None,
        'name': 'Facebook Creator', 'username': 'Facebook_' + suffix,
    })
    provider = dynamodb_table.get_item(Key={'pk': f'PROVIDER_USER#{issuer}#{facebook_sub}', 'sk': 'META'})['Item']
    facebook_id = provider['user_id']
    assert facebook_id != original['id']
    saved = get_dynamodb_user(facebook_id)
    if email:
        assert saved['user_email_pk'] == shared_email
    else:
        assert 'user_email_pk' not in saved
        contact_page = get(facebook, '/contacts')
        assert contact_page.status_code == 200, contact_page.text
        assert pq(contact_page.text)('input[name=email]').attr('value') == ''
    assert patch(facebook, f'/users/{original["id"]}', json={'name': 'Wrong account'}).status_code == 403
    edited = patch(facebook, f'/users/{facebook_id}', json={'name': 'Updated Facebook Creator'})
    assert edited.status_code == 200, edited.text
    assert get_dynamodb_user(facebook_id)['name'] == 'Updated Facebook Creator'
    assert get_dynamodb_user(original['id'])['name'] == 'Existing Google Creator'
    own_edit = patch(google, f'/users/{original["id"]}', json={'name': 'Still Google Creator'})
    assert own_edit.status_code == 200, own_edit.text
    assert get_dynamodb_user(facebook_id)['name'] == 'Updated Facebook Creator'


def test_google_signin_does_not_claim_facebook_account_by_unverified_email():
    suffix = uuid.uuid4().hex
    issuer, email = 'facebook-first-tests', f'facebook-first-{suffix}@example.com'
    facebook = get_logged_in_client({'sub': 'facebook-' + suffix, 'iss': issuer, 'email': email,
                                     'name': 'Facebook First'})
    original = get_dynamodb_user_by_email(email)
    assert 'email_match' not in original
    google_sub = 'google-' + suffix
    google = get_logged_in_client({'sub': google_sub, 'iss': issuer, 'email': email,
                                   'name': 'Separate Google'})
    provider = dynamodb_table.get_item(Key={'pk': f'PROVIDER_USER#{issuer}#{google_sub}', 'sk': 'META'})['Item']
    assert provider['user_id'] != original['id']
    assert patch(google, f'/users/{original["id"]}', json={'name': 'Wrong account'}).status_code == 403
    assert patch(facebook, f'/users/{original["id"]}', json={'name': 'Still Facebook'}).status_code == 200


def test_owner_can_connect_unused_login_and_sign_in_through_either_identity(guest_client):
    suffix = uuid.uuid4().hex
    issuer, original_sub = 'explicit-login-tests', 'original-' + suffix
    email = f'original-login-{suffix}@example.com'
    original_identity = {'sub': original_sub, 'iss': issuer, 'email': email, 'name': 'Original Login Creator'}
    owner = get_logged_in_client(original_identity)
    original = get_dynamodb_user_by_email(email)
    user_id = original['id']
    endpoint = f'/users/{user_id}/login-methods'
    assert post(guest_client, endpoint, json={}).status_code == 401
    assert get(guest_client, endpoint).status_code == 401
    availability = get(owner, endpoint)
    assert availability.status_code == 200
    assert availability.json() == {'configured': False}
    assert availability.headers['Cache-Control'] == 'no-store'
    outsider = get_logged_in_client({'sub': 'outsider-' + suffix, 'iss': issuer,
                                     'email': f'outsider-login-{suffix}@example.com', 'name': 'Outsider'})
    assert post(outsider, endpoint, json={}).status_code == 403
    assert get(outsider, endpoint).status_code == 403
    assert not pq(get(outsider, f'/users/{user_id}/edit').text)('#connect-login-method')
    editor = get(owner, f'/users/{user_id}/edit')
    assert editor.status_code == 200, editor.text
    assert pq(editor.text)('#connect-login-method')
    secondary = {'sub': 'secondary-' + suffix, 'iss': issuer,
                 'email': f'secondary-login-{suffix}@example.com', 'name': 'Secondary Provider Name'}
    fake_code = base64.urlsafe_b64encode(json.dumps(secondary).encode()).decode()
    begun = post(owner, endpoint, json={})
    assert begun.status_code == 200, begun.text
    assert begun.headers['Cache-Control'] == 'no-store'
    cookie = begun.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie and 'Path=/auth/login-link/callback' in cookie
    state = parse_qs(urlsplit(begun.json()['connect_url']).query)['state'][0]
    callback = '/auth/login-link/callback?' + urlencode({'state': state, 'code': fake_code})
    before = get_dynamodb_user(user_id)
    connected = get(owner, callback, allow_redirects=False)
    assert connected.status_code == 303, connected.text
    assert connected.headers['Location'].endswith(f'/users/{user_id}/edit?login_link=connected#integrations')
    assert connected.headers['Cache-Control'] == 'no-store'
    assert connected.headers['Referrer-Policy'] == 'no-referrer'
    assert 'Max-Age=0' in connected.headers['Set-Cookie']
    assert get_dynamodb_user(user_id) == before
    mapping = dynamodb_table.get_item(Key={'pk': f'PROVIDER_USER#{issuer}#{secondary["sub"]}', 'sk': 'META'})['Item']
    assert mapping['user_id'] == user_id
    replay = get(owner, callback, allow_redirects=False)
    assert replay.status_code == 303 and 'login_link=expired' in replay.headers['Location']
    secondary_client = get_logged_in_client(secondary)
    own_editor = get(secondary_client, f'/users/{user_id}/edit')
    assert own_editor.status_code == 200, own_editor.text
    assert pq(own_editor.text)('h1').text() == 'Edit Original Login Creator'
    assert patch(secondary_client, f'/users/{user_id}', json={'name': 'Connected Creator'}).status_code == 200
    assert get_dynamodb_user(user_id)['name'] == 'Connected Creator'
    assert get(get_logged_in_client(original_identity), f'/users/{user_id}/edit').status_code == 200


@pytest.mark.parametrize('providers', [('Google', 'SignInWithApple'), ('SignInWithApple', 'Google'),
                                       ('Google', 'Facebook'), ('COGNITO', 'Google')])
def test_same_email_signins_create_separate_accounts_without_explicit_connection(providers):
    suffix = uuid.uuid4().hex
    issuer, email = 'no-email-merge-tests', f'no-email-merge-{suffix}@example.com'
    first_sub, second_sub = [provider + '-' + suffix for provider in providers]
    first_identity = {'sub': first_sub, 'iss': issuer, 'email': email, 'name': 'First Creator'}
    first = get_logged_in_client(first_identity)
    first_user = get_dynamodb_user_by_email(email)
    second_identity = {'sub': second_sub, 'iss': issuer, 'email': email, 'name': 'Second Creator'}
    second = get_logged_in_client(second_identity)
    second_mapping = dynamodb_table.get_item(Key={'pk': f'PROVIDER_USER#{issuer}#{second_sub}', 'sk': 'META'})['Item']
    second_id = second_mapping['user_id']
    assert second_id != first_user['id']
    assert patch(second, f'/users/{first_user["id"]}', json={'name': 'Wrong account'}).status_code == 403
    assert patch(first, f'/users/{second_id}', json={'name': 'Wrong account'}).status_code == 403
    assert get_dynamodb_user(second_id)['name'] == 'Second Creator'
    assert get(get_logged_in_client(first_identity), f'/users/{first_user["id"]}/edit').status_code == 200
    assert get(get_logged_in_client(second_identity), f'/users/{second_id}/edit').status_code == 200


def test_crypto_support_wallets_save_select_and_render(guest_client):
    identity = uuid.uuid4().hex
    owner = get_logged_in_client({"sub": identity, "iss": "crypto-wallets", "email": f"{identity}@example.com"})
    owner_id = get_dynamodb_user_by_email(f"{identity}@example.com")["id"]
    profile = f"/users/{owner_id}"
    wallets = [
        {"id": "btc", "currency": "BTC", "network": "Bitcoin", "address": "bc1qexample",
         "instructions": "Bitcoin only <script>alert(1)</script>", "show_on_user_page": False},
        {"id": "eth", "currency": "ETH", "network": "Ethereum", "address": "0xExample", "instructions": ""},
        {"id": "custom", "currency": "CUSTOM", "network": "Custom chain", "address": "DExample", "instructions": ""},
    ]
    response = patch(owner, profile, json={"crypto_wallets": wallets})
    assert response.status_code == 200, response.text
    assert [wallet["address"] for wallet in get_dynamodb_user(owner_id)["crypto_wallets"]] == [wallet["address"] for wallet in wallets]
    editor = pq(get(owner, profile + "/edit").text)
    assert editor("#crypto-links [data-crypto-link]").length == 3
    assert editor('#crypto-btc-network option[selected]').attr("value") == "Bitcoin"
    assert editor('#crypto-new-link-network').attr("disabled") is not None
    assert editor('#crypto-new-link-currency-BTC').attr("checked") is None
    assert editor('[data-wallet-currency]:not([data-form-ignore])').length == 0
    assert editor('#crypto-custom-currency-Other').attr("checked") is not None
    assert editor('#crypto-custom-currency').attr("type") == "text"
    assert editor('#crypto-custom-currency').attr("value") == "CUSTOM"
    assert editor('#crypto-custom-custom-network').attr("value") == "Custom chain"
    assert editor('#crypto-custom-custom-network').attr("disabled") is None
    assert editor('#crypto-custom-network').attr("disabled") is not None
    assert editor('#crypto-btc-address').attr("placeholder")
    assert editor('label[for="crypto-btc-currency-BTC"] i.bi-currency-bitcoin[aria-hidden="true"]')
    assert editor('label[for="crypto-btc-currency-USDC"] i.bi-currency-dollar')
    assert not editor('label[for="crypto-btc-currency-ETH"] i')
    assert not editor('label[for="crypto-btc-currency-Other"] i')
    assert editor('#crypto-custom-currency').closest('[data-wallet-custom-currency]')('i.bi-coin')
    assert editor('#crypto-btc-currency').closest('[data-wallet-custom-currency]').attr('hidden') is not None
    assert editor('#crypto-btc-network').closest('.input-group')('i.bi-diagram-3')
    networks = json.loads(editor('#crypto-links [data-crypto-link]').eq(0).attr('data-wallet-networks'))
    for code, network in [('BNB', 'BNB Smart Chain'), ('XRP', 'XRP Ledger'), ('DOGE', 'Dogecoin'),
                          ('ADA', 'Cardano'), ('TRX', 'Tron'), ('AVAX', 'Avalanche C-Chain'), ('XMR', 'Monero')]:
        assert editor(f'#crypto-btc-currency-{code}')
        assert network in networks[code]
    assert patch(guest_client, profile, json={"crypto_wallets": []}).status_code == 401
    assert patch(owner, profile, json={"crypto_wallets": [{**wallets[0], "address": "bad address"}]}).status_code == 422

    data = {"title": "Crypto support " + identity, "content": "<p>Article with crypto support wallets.</p>" * 150, "category": "other",
            "tags": ["crypto"], "support_providers": [], "support_wallets": ["btc"]}
    assert post(owner, "/articles", json={**data, "support_wallets": ["not-owned"]}).status_code == 422
    created = post(owner, "/articles", json=data)
    assert created.status_code == 200, created.text
    article_id = json.loads(pq(get(owner, created.json()).text)('script[type="application/ld+json"]').text())["identifier"]
    endpoint = f"/articles/{article_id}"
    assert get_dynamodb_article(article_id)["support_wallets"] == ["btc"]
    admin = get_logged_in_client(root_user)
    assert patch(admin, endpoint, json={"support_wallets": ["not-owned"]}).status_code == 422
    assert post(admin, endpoint + "/status", json={"status": "published"}).status_code == 200

    def support():
        page = get(guest_client, created.json())
        assert page.status_code == 200, page.text
        return pq(page.text)("#creator-wallets")

    block = support()
    assert block('input[id^="support-wallet-"][readonly]').attr("value") == "bc1qexample"
    assert not block("script")
    assert "<script>alert(1)</script>" in block.text()
    assert block('button[aria-label="Copy BTC address"] i.bi-copy')
    assert block('label[for="support-wallet-btc"]').text() == 'Address:'
    toggle = block('#support-wallet-toggle-btc')
    assert toggle.text().strip() == "BTC wallet"
    assert toggle.attr('data-bs-toggle') == 'dropdown'
    assert toggle.attr('data-bs-auto-close') == 'outside'
    assert toggle.attr('aria-expanded') == 'false'
    panel = block('#support-wallet-menu-btc')
    assert toggle.attr('aria-controls') == panel.attr('id')
    assert panel.attr('aria-labelledby') == toggle.attr('id')
    assert panel('input[id^="support-wallet-"][readonly]').attr('value') == 'bc1qexample'
    assert panel('button[aria-label="Copy BTC address"]')
    assert panel('i.bi-diagram-3')
    assert panel('#support-network-btc').attr('value') == 'Bitcoin'
    assert panel('#support-instructions-btc[readonly]').text() == wallets[0]['instructions']
    assert panel('.input-group').length == 3
    assert panel('label[for="support-network-btc"]').text() == 'Network:'
    assert panel('label[for="support-instructions-btc"]').text() == 'Instructions:'
    assert "Bitcoin only <script>alert(1)</script>" in panel.text()
    assert not toggle('input')
    assert not block('#support-wallet-eth')
    profile_page = pq(get(guest_client, profile).text)
    assert not profile_page('#support-wallet-btc')
    assert profile_page('#support-wallet-eth')
    assert not profile_page('#support-instructions-eth')
    assert patch(owner, endpoint, json={"support_wallets": []}).status_code == 200
    assert not support()
    assert patch(owner, endpoint, json={"support_wallets": None}).status_code == 200
    assert support()('input[id^="support-wallet-"][readonly]').length == 3
    assert patch(owner, profile, json={"crypto_wallets": []}).status_code == 200
    assert not support()

def test_direct_messages_are_private_idempotent_paginated_and_block_aware(guest_client):
    from concurrent.futures import ThreadPoolExecutor

    suffix = uuid.uuid4().hex
    people = []
    for role in ("sender", "recipient", "outsider", "admin"):
        identity = {"sub": f"{role}-{suffix}", "iss": "private-messages", "email": f"{role}-{suffix}@example.com"}
        client = get_logged_in_client(identity)
        people.append((client, get_dynamodb_user_by_email(identity["email"])["id"]))
    (sender, sender_id), (recipient, recipient_id), (outsider, outsider_id), (admin, admin_id) = people
    set_dynamodb_user_permissions(admin_id, ["root"])
    endpoint = f"/messages/{recipient_id}"
    history = endpoint + "/history"
    assert get(guest_client, "/messages").status_code == 401
    assert get(guest_client, endpoint).status_code == 401
    assert get(guest_client, history).status_code == 401
    assert post(guest_client, endpoint, json={"text": "hello", "message_id": str(uuid.uuid4())}).status_code == 401
    empty = get(sender, endpoint)
    assert empty.status_code == 200, empty.text
    assert "no-store" in empty.headers["Cache-Control"]
    assert empty.headers["X-Robots-Tag"] == "noindex, nofollow"
    empty_doc = pq(empty.text)
    assert empty_doc('#direct-message-text')
    assert empty_doc('#direct-message-text').attr('maxlength') is None
    assert empty_doc('main h1').text() == 'Messages with ' + get_dynamodb_user(recipient_id)['name'].split()[0]
    assert empty_doc('#direct-conversation > p.text-body-secondary').eq(0).text() == (
        'Read and reply to your private messages with ' + get_dynamodb_user(recipient_id)['name'].split()[0] + '.'
    )
    assert empty_doc('#direct-conversation .user h2').text() == get_dynamodb_user(recipient_id)['name']
    assert empty_doc(f'#direct-conversation .user a[href="{get_user_href(get_dynamodb_user(recipient_id))}"]')
    assert not empty_doc('#direct-conversation a[href="/messages"]')
    assert not empty_doc('#direct-conversation a:contains("View profile")')
    assert get_dynamodb_user(sender_id)['name'] in empty_doc('#user-menu-toggle').text()
    assert empty_doc(f'#user-menu a[href="{get_user_href(get_dynamodb_user(sender_id))}"]')
    assert empty_doc('#user-menu a[href="/messages"]').attr('aria-current') == 'page'
    breadcrumbs = empty_doc('nav[aria-label="breadcrumb"]')
    assert breadcrumbs('a[href="/"]').text() == 'Home'
    assert breadcrumbs('a[href="/messages"]').text() == 'Messages'
    assert breadcrumbs('[aria-current="page"]').text() == get_dynamodb_user(recipient_id)['name']
    profile = pq(get(sender, f"/users/{recipient_id}").text)
    assert profile(f'a[href="{endpoint}"]').text() == 'Message'
    original_timestamp = get_dynamodb_user(sender_id).get('updated_at')
    payload = {"text": "Hello <script>alert(1)</script>\nHow is practice going?", "message_id": str(uuid.uuid4())}
    sent = post(sender, endpoint, json=payload)
    assert sent.status_code == 200, sent.text
    first = sent.json()["message"]
    assert get_dynamodb_user(sender_id).get('updated_at') == original_timestamp
    assert post(sender, endpoint, json=payload).json()["message"] == first
    assert post(sender, endpoint, json={**payload, "text": "changed"}).status_code == 409
    assert post(sender, endpoint, json={**payload, "sender_id": outsider_id}).status_code == 422
    assert post(sender, endpoint, json={"text": " ", "message_id": str(uuid.uuid4())}).status_code == 422
    assert post(sender, endpoint, json={"text": "x" * 5001, "message_id": str(uuid.uuid4())}).status_code == 422
    assert post(sender, endpoint, json={**payload, "message_id": "invalid"}).status_code == 422
    assert post(sender, endpoint, json={**payload, "text": "bad\x00text"}).status_code == 422
    assert post(sender, endpoint, json=payload, headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert post(sender, endpoint, data={"text": "form", "message_id": str(uuid.uuid4())}).status_code == 415
    assert get(sender, history + '?limit=0').status_code == 422
    assert get(sender, history + '?after=invalid').status_code == 422
    assert get(sender, history + '?offset=garbage').status_code == 422
    assert post(sender, f'/messages/{sender_id}', json=payload).status_code == 422
    assert post(sender, '/messages/missing-user', json=payload).status_code == 404

    def inbox(client):
        page = get(client, '/messages')
        assert page.status_code == 200, page.text
        assert 'no-store' in page.headers['Cache-Control']
        doc = pq(page.text)
        assert doc('#user-menu-toggle')
        assert doc('main h1').text() == 'Messages'
        assert doc('main > p.text-body-secondary').text() == "Private messages with other members. Start a conversation from a user's profile."
        assert doc('#user-menu a[href="/messages"]').attr('aria-current') == 'page'
        breadcrumbs = doc('nav[aria-label="breadcrumb"]')
        assert breadcrumbs('a[href="/"]').text() == 'Home'
        assert breadcrumbs('[aria-current="page"]').text() == 'Messages'
        return doc

    assert inbox(recipient)(f'[data-conversation-user="{sender_id}"][data-unread="true"]')
    page = pq(get(recipient, f'/messages/{sender_id}').text)
    assert page('#direct-message-list').text().find('Hello <script>alert(1)</script>') >= 0
    assert not page('#direct-message-list script')
    assert len(get(sender, history).json()['messages']) == 1
    for client in (outsider, admin):
        assert get(client, history).json()['messages'] == []
        assert not inbox(client)(f'[data-conversation-user="{sender_id}"]')
        assert post(client, endpoint + '/read', json={'message_key': first['key']}).status_code == 404
    read_endpoint = f'/messages/{sender_id}/read'
    assert post(recipient, read_endpoint, json={'message_key': first['key']}).status_code == 204
    assert not inbox(recipient)('[data-unread="true"]')
    second_response = post(sender, endpoint, json={'text': 'Second message', 'message_id': str(uuid.uuid4())})
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()['message']
    reply = post(recipient, f'/messages/{sender_id}', json={'text': 'A reply', 'message_id': str(uuid.uuid4())})
    assert reply.status_code == 200, reply.text
    assert inbox(recipient)('[data-unread="true"]')  # Sending must not clear unseen incoming messages.
    assert post(recipient, read_endpoint, json={'message_key': first['key']}).status_code == 204
    assert inbox(recipient)('[data-unread="true"]')
    assert post(recipient, read_endpoint, json={'message_key': second['key']}).status_code == 204
    assert not inbox(recipient)('[data-unread="true"]')

    newest = get(sender, history + '?limit=1').json()
    assert newest['messages'][0]['text'] == 'A reply'
    older = get(sender, history + '?limit=1&offset=' + quote(newest['offset'])).json()
    assert older['messages'][0]['id'] == second['id']
    assert get(sender, f'/messages/{outsider_id}/history?offset=' + quote(newest['offset'])).status_code == 422
    newer = get(sender, history + '?after=' + quote(first['key'])).json()
    assert [item['text'] for item in newer['messages']] == ['Second message', 'A reply']
    third_thread = post(sender, f'/messages/{outsider_id}', json={'text': 'Separate conversation', 'message_id': str(uuid.uuid4())})
    assert third_thread.status_code == 200
    first_inbox = pq(get(sender, '/messages?limit=1').text)
    next_button = first_inbox('.btn-load-more[data-container="#direct-conversations"]')
    next_cursor = first_inbox('#direct-conversations > [data-offset]').attr('data-offset')
    assert next_button and next_cursor
    next_path = '/messages/inbox/fragment?limit=1&offset=' + quote(next_cursor)
    assert get(sender, next_path).status_code == 200
    assert get(recipient, next_path).status_code == 422
    assert not first_inbox('a[href*="offset="]')
    assert not inbox(recipient)(f'[data-conversation-user="{outsider_id}"]')

    assert post(recipient, f'/users/{sender_id}/impression', json={'action': 'block'}).status_code == 200
    for client, peer in ((sender, recipient_id), (recipient, sender_id)):
        blocked = post(client, f'/messages/{peer}', json={'text': 'Blocked delivery', 'message_id': str(uuid.uuid4())})
        assert blocked.status_code == 403, blocked.text
        assert get(client, f'/messages/{peer}').status_code == 200
        assert not pq(get(client, f'/messages/{peer}').text)('#direct-message-form')
    assert post(recipient, f'/users/{sender_id}/impression', json={'action': 'block'}).status_code == 200

    def concurrent_send(index):
        client = get_guest_client()
        client.cookies.update(sender.cookies)
        response = post(client, endpoint, json={'text': f'Concurrent {index}', 'message_id': str(uuid.uuid4())})
        return response.status_code, response.text

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(concurrent_send, range(8)))
    assert all(code == 200 for code, _ in results), results
    all_messages = get(sender, history + '?limit=40').json()['messages']
    assert len(all_messages) == 11
    assert len({item['id'] for item in all_messages}) == 11
    assert [item['key'] for item in all_messages] == sorted(item['key'] for item in all_messages)
    assert inbox(recipient)(f'[data-conversation-user="{sender_id}"]').length == 1
    status_change = post(admin, f'/users/{recipient_id}/status', json={'status': 'banned', 'comment': 'Test restriction'})
    assert status_change.status_code == 200, status_change.text
    assert post(sender, endpoint, json={'text': 'Inactive recipient', 'message_id': str(uuid.uuid4())}).status_code == 403
    assert get(sender, endpoint).status_code == 200
    assert get(recipient, '/messages').status_code == 403

def test_new_direct_message_email_delivery_preferences_and_retries(guest_client):
    suffix = uuid.uuid4().hex
    sender_email = f'dm-email-sender-{suffix}@example.com'
    recipient_email = f'dm-email-recipient-{suffix}@example.com'
    sender = get_logged_in_client({'sub': 'sender-' + suffix, 'iss': 'direct-email-tests', 'email': sender_email})
    recipient = get_logged_in_client({'sub': 'recipient-' + suffix, 'iss': 'direct-email-tests', 'email': recipient_email})
    sender_id = get_dynamodb_user_by_email(sender_email)['id']
    recipient_id = get_dynamodb_user_by_email(recipient_email)['id']
    profile_url = f'/users/{recipient_id}'
    endpoint = f'/messages/{recipient_id}'
    settings = pq(get(recipient, profile_url + '/edit').text)
    assert settings('#notifications #notify_direct_messages').is_(':checked')
    assert settings('label[for="notify_direct_messages"]').text() == 'New private messages'
    assert patch(sender, profile_url, json={'notify_direct_messages': False}).status_code == 403
    assert patch(guest_client, profile_url, json={'notify_direct_messages': False}).status_code == 401
    for invalid in (None, 0, 'false'):
        assert patch(recipient, profile_url, json={'notify_direct_messages': invalid}).status_code == 422

    directory = Path('/app/.emails')
    existing_paths = {path.name for path in directory.glob('*.eml')}

    def emails():
        messages = {}
        for path in directory.glob('*.eml'):
            if path.name in existing_paths:
                continue
            item = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            if item['To'] == recipient_email and item['Subject'] == 'You have a new direct message':
                messages[path.name] = item
        return messages

    before = emails()
    payload = {'text': f'Private content stays in the app <script>{suffix}</script>', 'message_id': str(uuid.uuid4())}
    sent = post(sender, endpoint, json=payload)
    assert sent.status_code == 200, sent.text
    after = emails()
    new_paths = after.keys() - before.keys()
    assert len(new_paths) == 1
    notification = after[next(iter(new_paths))]
    plain = notification.get_body('plain').get_content()
    html = notification.get_body('html').get_content()
    assert get_dynamodb_user(sender_id)['name'] in plain
    assert payload['text'] not in plain and payload['text'] not in html
    assert f'/messages/{sender_id}' in plain
    doc = pq(html)
    assert any(urlsplit(link.attr('href')).path == f'/messages/{sender_id}' for link in doc('a[href]').items())
    assert any(urlsplit(link.attr('href')).path == profile_url + '/edit'
               and urlsplit(link.attr('href')).fragment == 'notifications' for link in doc('a[href]').items())
    assert post(sender, endpoint, json=payload).status_code == 200
    assert emails().keys() == after.keys()
    assert post(sender, endpoint, json={**payload, 'text': 'Conflicting retry'}).status_code == 409
    assert emails().keys() == after.keys()
    assert post(sender, endpoint, json={'text': 'Invalid UUID', 'message_id': 'invalid'}).status_code == 422
    assert emails().keys() == after.keys()

    assert patch(recipient, profile_url, json={'notify_direct_messages': False}).status_code == 200
    assert get_dynamodb_user(recipient_id)['notify_direct_messages'] is False
    assert not pq(get(recipient, profile_url + '/edit').text)('#notify_direct_messages').is_(':checked')
    disabled = post(sender, endpoint, json={'text': 'Still delivered in the app', 'message_id': str(uuid.uuid4())})
    assert disabled.status_code == 200, disabled.text
    assert emails().keys() == after.keys()
    assert patch(recipient, profile_url, json={'notify_direct_messages': True}).status_code == 200
    enabled = post(sender, endpoint, json={'text': 'Notifications are enabled again', 'message_id': str(uuid.uuid4())})
    assert enabled.status_code == 200, enabled.text
    enabled_messages = emails()
    assert len(enabled_messages.keys() - after.keys()) == 1
    assert post(recipient, f'/users/{sender_id}/impression', json={'action': 'block'}).status_code == 200
    blocked = post(sender, endpoint, json={'text': 'Blocked message', 'message_id': str(uuid.uuid4())})
    assert blocked.status_code == 403, blocked.text
    assert emails().keys() == enabled_messages.keys()

def test_presence_routes_all_account_updates_to_site_notifications_with_atomic_cached_counts(guest_client):
    from concurrent.futures import ThreadPoolExecutor

    suffix = uuid.uuid4().hex
    receiver_email = f'site-receiver-{suffix}@example.com'
    sender = get_logged_in_client({'sub': 'site-sender-' + suffix, 'iss': 'site-notifications',
                                   'email': f'site-sender-{suffix}@example.com'})
    receiver = get_logged_in_client({'sub': 'site-receiver-' + suffix, 'iss': 'site-notifications', 'email': receiver_email})
    outsider = get_logged_in_client({'sub': 'site-outsider-' + suffix, 'iss': 'site-notifications',
                                     'email': f'site-outsider-{suffix}@example.com'})
    sender_id = get_dynamodb_user_by_email(f'site-sender-{suffix}@example.com')['id']
    receiver_id = get_dynamodb_user_by_email(receiver_email)['id']
    endpoint = f'/messages/{receiver_id}'
    user_key = {'pk': f'USER#{receiver_id}', 'sk': 'META'}
    initial = get_dynamodb_user(receiver_id)
    initial_emails = {path.name for path in Path('/app/.emails').glob('*.eml')}
    assert get(guest_client, '/notifications').status_code == 401
    assert get(guest_client, '/notifications/status').status_code == 401
    assert post(guest_client, '/presence', json={}).status_code == 401
    assert post(receiver, '/presence', json={'last_seen_at': 9999999999999}).status_code == 422
    assert post(receiver, '/presence', json={}, headers={'Origin': 'https://untrusted.example'}).status_code == 403
    first_beat = post(receiver, '/presence', json={})
    assert first_beat.status_code == 200, first_beat.text
    heartbeat = first_beat.json()
    assert heartbeat['user_id'] == receiver_id and heartbeat['last_seen_at'] > 0
    repeated = post(receiver, '/presence', json={})
    assert repeated.json()['last_seen_at'] == heartbeat['last_seen_at']
    assert get_dynamodb_user(receiver_id).get('updated_at') == initial.get('updated_at')
    assert heartbeat['unread_count'] == 0

    payload = {'text': 'Online delivery without email ' + suffix, 'message_id': str(uuid.uuid4())}
    sent = post(sender, endpoint, json=payload)
    assert sent.status_code == 200, sent.text
    history = get(receiver, '/notifications/history?limit=1')
    assert history.status_code == 200, history.text
    assert 'no-store' in history.headers['Cache-Control']
    entry = history.json()['notifications'][0]
    assert entry['delivery'] == 'web' and entry['is_read'] is False
    assert entry['url'] == f'/messages/{sender_id}'
    assert payload['text'] not in json.dumps(entry)
    state = get(receiver, '/notifications/status').json()
    assert state['unread_count'] == 1 and state['revision'] == 1
    user = get_dynamodb_user(receiver_id)
    assert user['unread_notifications_count'] == 1
    assert user.get('updated_at') == initial.get('updated_at')
    assert post(sender, endpoint, json=payload).status_code == 200
    assert get(receiver, '/notifications/status').json()['unread_count'] == 1
    bell = pq(get(receiver, '/').text)('#site-notifications')
    assert bell.attr('data-unread') == '1'
    assert bell('[data-unread-notifications]').text() == '1'
    center = get(receiver, '/notifications')
    doc = pq(center.text)
    assert doc('main h1').text() == 'Notifications'
    assert doc('nav[aria-label="breadcrumb"] [aria-current="page"]').text() == 'Notifications'
    assert center.headers['X-Robots-Tag'] == 'noindex, nofollow'
    assert doc(f'[data-notification-item="{entry["id"]}"]')
    assert get(outsider, '/notifications/history').json()['notifications'] == []
    read_url = f'/notifications/{entry["id"]}/read'
    assert post(outsider, read_url, json={}).status_code == 404

    def read_once(_):
        client = get_guest_client()
        client.cookies.update(receiver.cookies)
        return post(client, read_url, json={}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(code == 200 for code in pool.map(read_once, range(6)))
    assert get(receiver, '/notifications/status').json()['unread_count'] == 0
    assert get(receiver, '/notifications/status').json()['revision'] == 2
    assert get(receiver, '/notifications/history').json()['notifications'][0]['is_read'] is True

    # Other account notification types use the same online routing.
    followed = post(sender, f'/users/{receiver_id}/impression', json={'action': 'follow'})
    assert followed.status_code == 200, followed.text
    assert get(receiver, '/notifications/status').json()['unread_count'] == 1
    assert post(receiver, '/articles', json={'title': 'Site notifications ' + suffix,
        'content': '<p>An article for live notification routing.</p>' * 150, 'category': 'other', 'tags': ['site-alerts']}).status_code == 200
    owned = get_dynamodb_user(receiver_id)
    # No notification delivery is allowed to overwrite profile timestamps.
    before_presence = owned.get('updated_at')
    assert post(receiver, '/presence', json={}).status_code == 200
    assert get_dynamodb_user(receiver_id).get('updated_at') == before_presence
    newer = post(sender, endpoint, json={'text': 'A second online message', 'message_id': str(uuid.uuid4())})
    assert newer.status_code == 200
    page = get(receiver, '/notifications/history?limit=1').json()
    assert page['offset']
    assert get(outsider, '/notifications/history?offset=' + quote(page['offset'])).status_code == 422
    assert get(receiver, '/notifications/history?limit=1000').status_code == 422

    # Expire only the test fixture's presence: the next event should also produce email.
    dynamodb_table.update_item(Key=user_key, UpdateExpression='SET last_seen_at = :seen',
                               ExpressionAttributeValues={':seen': int(time.time() * 1000) - 10 * 60 * 1000})
    offline_payload = {'text': 'Offline delivery ' + suffix, 'message_id': str(uuid.uuid4())}
    assert post(sender, endpoint, json=offline_payload).status_code == 200
    latest = get(receiver, '/notifications/history?limit=1').json()['notifications'][0]
    assert latest['delivery'] == 'email'
    delivered = []
    for path in Path('/app/.emails').glob('*.eml'):
        if path.name in initial_emails:
            continue
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        if message['To'] == receiver_email:
            delivered.append(message)
    assert len(delivered) == 1
    assert delivered[0]['Subject'] == 'You have a new direct message'
    before_count = get(receiver, '/notifications/status').json()['unread_count']
    assert patch(receiver, f'/users/{receiver_id}', json={'notify_direct_messages': False}).status_code == 200
    assert post(sender, endpoint, json={'text': 'Opted out of both channels', 'message_id': str(uuid.uuid4())}).status_code == 200
    assert get(receiver, '/notifications/status').json()['unread_count'] == before_count

def test_notification_center_autoloads_private_shared_rows_with_scoped_cursors(guest_client):
    suffix = uuid.uuid4().hex
    email = f'notification-scroll-{suffix}@example.com'
    owner = get_logged_in_client({'sub': 'notification-scroll-' + suffix, 'iss': 'notification-scroll', 'email': email})
    outsider = get_logged_in_client({'sub': 'notification-scroll-outsider-' + suffix,
                                    'iss': 'notification-scroll', 'email': f'outsider-{email}'})
    user_id = get_dynamodb_user_by_email(email)['id']
    partition = f'USER#{user_id}'
    timestamp = int(time.time() * 1000)
    ids = []
    with dynamodb_table.batch_writer() as batch:
        for index in range(25):
            identifier = f'{index:064x}'
            ids.append(identifier)
            key = f'SITE_NOTIFICATION#{timestamp + index:020d}#{identifier}'
            batch.put_item(Item={'pk': partition, 'sk': key, 'id': identifier, 'title': f'Update {index}',
                                 'message': 'Private <script>notification</script>', 'detail': 'Details <safe>',
                                 'url': '/notifications', 'icon': 'bell', 'is_read': False,
                                 'delivery': 'web', 'created_at': timestamp + index,
                                 **({'title': 'New reaction to your article', 'message': 'Member disliked your article “Example”.',
                                     'icon': 'hand-thumbs-up'} if index == 4 else {})})
            batch.put_item(Item={'pk': partition, 'sk': f'SITE_NOTIFICATION_ID#{identifier}',
                                 'notification_key': key})
    dynamodb_table.update_item(Key={'pk': partition, 'sk': 'META'},
                               UpdateExpression='SET unread_notifications_count = :count, notifications_revision = :count',
                               ExpressionAttributeValues={':count': 25})
    page = get(owner, '/notifications')
    assert page.status_code == 200, page.text
    doc = pq(page.text)
    first = doc('#notification-history [data-notification-item]')
    assert len(first) == 20
    button = doc('.btn-load-more[data-auto-click]')
    assert button.attr('data-limit') == '20'
    assert button.attr('data-container') == '#notification-history'
    assert dict(parse_qsl(urlsplit(button.attr('data-url')).query))['format'] == 'html'
    assert first.eq(-1).attr('data-offset') == button.attr('data-offset')
    assert not first.eq(0).attr('data-offset')
    endpoint = '/notifications/history?format=html&limit=20&offset=' + quote(button.attr('data-offset'))
    remaining = get(owner, endpoint)
    assert remaining.status_code == 200, remaining.text
    assert remaining.headers['Content-Type'].startswith('text/html')
    assert 'no-store' in remaining.headers['Cache-Control']
    rows = pq(remaining.text)('[data-notification-item]')
    assert len(rows) == 5
    assert rows.eq(0)('.bi-hand-thumbs-down')
    json_page = get(owner, endpoint.replace('format=html&', '')).json()
    assert json_page['notifications'][0]['icon'] == 'hand-thumbs-down'
    rendered_ids = [row.get('data-notification-item') for row in first] + [row.get('data-notification-item') for row in rows]
    assert rendered_ids == list(reversed(ids))
    assert not rows.eq(-1).attr('data-offset')
    assert not pq(remaining.text)('script')
    assert 'Private &lt;script&gt;notification&lt;/script&gt;' in remaining.text
    assert rows.eq(0)('[data-read-notification]').attr('data-read-notification') == ids[4]
    assert get(guest_client, endpoint).status_code == 401
    assert get(outsider, endpoint).status_code == 422
    assert not pq(get(outsider, '/notifications/history?format=html').text)('[data-notification-item]')
    assert get(owner, '/notifications/history?format=html&limit=0').status_code == 422
    assert pq(get(owner, '/notifications?limit=7').text)('.btn-load-more').attr('data-limit') == '7'
    read = post(owner, f'/notifications/{ids[4]}/read', json={})
    assert read.status_code == 200 and read.json()['unread_count'] == 24
    assert 'data-read-notification="' + ids[4] + '"' not in get(owner, endpoint).text
    # The dropdown still consumes the same endpoint as JSON by default.
    assert len(get(owner, '/notifications/history?limit=10').json()['notifications']) == 10


def test_reports_count_targets_and_article_owner_atomically_without_changing_dates(guest_client):
    from concurrent.futures import ThreadPoolExecutor
    suffix = uuid.uuid4().hex
    identity = {'sub': 'report-owner-' + suffix, 'iss': 'report-tests',
                'email': f'report-owner-{suffix}@example.com', 'name': 'Report Target Creator'}
    owner = get_logged_in_client(identity)
    owner_id = get_logged_in_user_id(identity)
    set_dynamodb_user_permissions(owner_id, ['root'])
    articles = []
    for index in range(2):
        created = post(owner, '/articles', json={
            'title': f'Report target {suffix} {index}', 'content': ARTICLE_CONTENT,
            'category': 'distributed-systems', 'tags': ['report-test'],
        })
        assert created.status_code == 200, created.text
        page = get(owner, created.json())
        owner_page = pq(page.text)
        assert not owner_page('.article-report')
        assert owner_page('script[src*="just-validate"]')
        status_path = urlsplit(owner_page('#article-status-form').attr('action')).path
        article_id = status_path.split('/')[2]
        report_path = f'/articles/{article_id}/report'
        articles.append((article_id, created.json(), report_path))
        assert post(owner, f'/articles/{article_id}/status', json={'status': 'unpublished'}).status_code == 200

    payload = {'name': 'Example Reporter', 'email': 'reporter@example.com',
               'message': f'Please review this resource. Reference: {suffix}'}
    assert not pq(get(owner, f'/users/{owner_id}').text)('.user-report')
    assert post(owner, f'/users/{owner_id}/report', json=payload).status_code == 403
    for _, _, report_path in articles:
        assert post(owner, report_path, json=payload).status_code == 403

    # An invisible draft cannot be reported by guessing its ID.
    assert post(guest_client, articles[0][2], json=payload).status_code == 404
    assert get_dynamodb_article(articles[0][0]).get('reports_count', 0) == 0
    assert get_dynamodb_user(owner_id).get('article_reports_count', 0) == 0
    for article_id, _, _ in articles:
        assert post(owner, f'/articles/{article_id}/status', json={'status': 'published'}).status_code == 200

    before_articles = {article_id: get_dynamodb_article(article_id) for article_id, _, _ in articles}
    before_owner = get_dynamodb_user(owner_id)
    user_page = pq(get(guest_client, f'/users/{owner_id}').text)
    assert user_page('script[src*="just-validate"]')
    user_report_path = urlsplit(user_page('#user-report-form').attr('action')).path
    assert user_report_path == f'/users/{owner_id}/report'
    assert post(guest_client, user_report_path, json=payload).status_code == 204

    def send_report(index):
        with get_guest_client() as client:
            response = post(client, articles[index % 2][2], json=payload)
            assert response.status_code == 204, response.text

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(send_report, range(8)))

    for article_id, article_path, report_path in articles:
        after = get_dynamodb_article(article_id)
        assert after.pop('reports_count') == 4
        assert after == before_articles[article_id]
        metadata = pq(get(guest_client, article_path).text)('.article-meta').eq(0)
        assert metadata('[title="Reports count"]').text() == '4 Reports'
        for field, value in [('message', 'bad'), ('email', 'not-an-email'), ('name', None)]:
            invalid = post(guest_client, report_path, json={**payload, field: value})
            assert invalid.status_code == 422
            assert list(invalid.json()['details']) == [field]
            assert get_dynamodb_article(article_id)['reports_count'] == 4

    after_owner = get_dynamodb_user(owner_id)
    assert after_owner.pop('reports_count') == 1
    assert after_owner.pop('article_reports_count') == 8
    assert after_owner == before_owner
    metadata = pq(get(guest_client, f'/users/{owner_id}').text)('.user-meta').eq(0)
    assert metadata('[title="Profile reports count"]').text() == '1 Reports'
    assert not metadata('[title="Article reports received count"]')
    owner_metadata = pq(get(owner, f'/users/{owner_id}').text)('.user-meta').eq(0)
    assert owner_metadata('[title="Article reports received count"]').text() == '8 Article reports'

    # Free-form contacts never increment report counters, even if their message says "report".
    assert post(guest_client, '/contacts/message', json=payload).status_code == 204
    assert post(guest_client, '/users/missing-report-user/report', json=payload).status_code == 404
    assert post(guest_client, '/articles/missing-report-article/report', json=payload).status_code == 404
    saved, cursor = [], None
    while True:
        page = dynamodb_table.scan(
            FilterExpression='#message = :message', ExpressionAttributeNames={'#message': 'message'},
            ExpressionAttributeValues={':message': payload['message']},
            **({'ExclusiveStartKey': cursor} if cursor else {}),
        )
        saved.extend(page['Items'])
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            break
    assert len(saved) == 10
    assert sum(row.get('target_user_id') == owner_id for row in saved) == 1
    assert sum(row.get('article_id') in before_articles for row in saved) == 8
    assert get_dynamodb_user(owner_id)['reports_count'] == 1
    assert get_dynamodb_user(owner_id)['article_reports_count'] == 8


def test_home_article_finder_uses_public_categories_tags_and_existing_listings(guest_client):
    suffix = uuid.uuid4().hex[:10]
    identity = {'sub': 'finder-' + suffix, 'iss': 'finder-tests',
                'email': f'finder-{suffix}@example.com', 'name': 'Finder Creator'}
    owner = get_logged_in_client(identity)
    set_dynamodb_user_permissions(get_logged_in_user_id(identity), ['root'])
    public_category = 'finder-public-' + suffix
    draft_category = 'finder-draft-' + suffix
    public_name = 'API <Design> & "guide"'
    for slug, name in [(public_category, public_name), (draft_category, 'Draft category')]:
        dynamodb_table.put_item(Item={
            'pk': 'CATEGORY', 'sk': slug, 'category_slug': slug, 'name': name,
            'description': 'Homepage finder regression.', 'published_articles_count': 0,
            'created_at': int(time.time() * 1000),
        })
    public_tag = 'finder-public-' + suffix
    draft_tag = 'finder-draft-' + suffix
    public_title = 'Public finder article ' + suffix
    draft_title = 'Private finder article ' + suffix
    for category, tag, title, published in [
        (public_category, public_tag, public_title, True),
        (draft_category, draft_tag, draft_title, False),
    ]:
        created = post(owner, '/articles', json={
            'title': title, 'content': ARTICLE_CONTENT, 'category': category, 'tags': [tag],
        })
        assert created.status_code == 200, created.text
        if published:
            page = pq(get(owner, created.json()).text)
            status_path = urlsplit(page('#article-status-form').attr('action')).path
            response = post(owner, status_path, json={'status': 'published'})
            assert response.status_code == 200, response.text

    homepage = get_index(guest_client)
    form = homepage('#home-search-form')
    search = form('#home-search')
    assert form.attr('role') == 'search'
    assert homepage('script[src*="tagify"]')
    assert not search.is_('[required], [minlength], [maxlength]')
    categories = {item['slug']: item for item in json.loads(search.attr('data-categories'))}
    assert categories[public_category] == {
        'slug': public_category, 'name': public_name, 'published_articles_count': 1,
    }
    assert draft_category not in categories
    assert all(item['published_articles_count'] > 0 for item in categories.values())
    article_count = homepage('#homepage-article-count')
    total = sum(item['published_articles_count'] for item in categories.values())
    assert article_count('.badge').text() == f'{total:,}'
    assert 'published articles to explore' in article_count.text()
    assert 'Meet the community' in article_count.parent().text()
    listing_path = urlsplit(form.attr('action')).path
    assert listing_path == '/articles'
    assert form(f'a[href="{form.attr("action")}"]')
    write_link = form('a').eq(1)
    assert 'nofollow' in write_link.attr('rel').split()
    assert parse_qs(urlsplit(write_link.attr('href')).query)['redirect_url'] == ['/articles/new']

    tags_path = urlsplit(search.attr('data-tags-url')).path
    public_tags = get(guest_client, tags_path + '?' + urlencode({
        'prefix': public_tag, 'limit': 4, 'published_only': 'true',
    }))
    assert public_tags.status_code == 200, public_tags.text
    assert [(tag['slug'], tag['articles_count']) for tag in public_tags.json()] == [(public_tag, 1)]
    assert get(guest_client, tags_path + '?' + urlencode({
        'prefix': draft_tag, 'limit': 4, 'published_only': 'true',
    })).json() == []
    for params in [{'category': public_category}, {'tags': public_tag}]:
        response = get(guest_client, listing_path + '?' + urlencode(params))
        assert response.status_code == 200, response.text
        listing = pq(response.text)
        filters = listing('#article-filters')
        assert filters('#category-filter option[value=""]').text() == 'All categories'
        assert filters(f'#category-filter option[value="{public_category}"]').text() == public_name
        assert filters(f'#category-filter option[value="{draft_category}"]')
        assert not filters('input[name="category"]')
        selected_category = filters('#category-filter option[selected]').attr('value')
        assert selected_category == params.get('category')
        assert filters('input[name="type"]').val() == 'latest'
        assert filters('input[name="status"]').val() == 'published'
        articles = listing('#articles')
        assert public_title in articles.text()
        assert draft_title not in articles.text()

    owner_form = get_index(owner)('#home-search-form')
    assert urlsplit(owner_form('a').eq(1).attr('href')).path == '/articles/new'
    draft_listing = get(owner, listing_path + '?' + urlencode({
        'category': draft_category, 'status': 'unpublished', 'type': 'popular', 'tags': draft_tag,
    }))
    assert draft_listing.status_code == 200, draft_listing.text
    draft_doc = pq(draft_listing.text)
    assert draft_title in draft_doc('#articles').text()
    assert draft_doc('#category-filter option[selected]').val() == draft_category
    assert draft_doc('#article-filters input[name="status"]').val() == 'unpublished'
    assert draft_doc('#article-filters input[name="type"]').val() == 'popular'


def test_article_category_names_follow_category_changes_and_backfill_is_guarded(guest_client, capsys):
    from botocore.exceptions import ClientError
    from backfill_article_category_names import backfill_article

    suffix = uuid.uuid4().hex[:10]
    identity = {'sub': 'category-snapshot-' + suffix, 'iss': 'category-snapshot-tests',
                'email': f'category-snapshot-{suffix}@example.com', 'name': 'Category Snapshot Creator'}
    owner = get_logged_in_client(identity)
    set_dynamodb_user_permissions(get_logged_in_user_id(identity), ['root'])
    original_category_name = dynamodb_table.get_item(Key={
        'pk': 'CATEGORY', 'sk': 'distributed-systems',
    })['Item']['name']
    created = post(owner, '/articles', json={
        'title': 'Category snapshot ' + suffix, 'content': ARTICLE_CONTENT,
        'category': 'distributed-systems', 'tags': ['snapshot-test'],
    })
    assert created.status_code == 200, created.text
    owner_page = pq(get(owner, created.json()).text)
    status_path = urlsplit(owner_page('#article-status-form').attr('action')).path
    article_id = status_path.split('/')[2]
    key = {'pk': 'POST#' + article_id, 'sk': 'META'}
    item = dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item']
    assert item['category_name'] == original_category_name
    changed = patch(owner, f'/articles/{article_id}', json={'category': 'caching'})
    assert changed.status_code == 200, changed.text
    item = dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item']
    assert item['category'] == 'caching'
    assert item['category_name'] == 'Caching'
    card_page = pq(get(owner, f'/users/{item["user_id"]}/articles-fragment?status=unpublished').text)
    assert card_page('.article-badges a[href="/articles?category=caching"]').text() == 'Caching'

    dynamodb_table.update_item(Key=key, UpdateExpression='REMOVE category_name')
    legacy = dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item']
    names = {'caching': 'Caching', 'distributed-systems': 'Distributed Systems'}
    assert backfill_article(dynamodb_table, legacy, names) == 'planned'
    assert dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item'] == legacy
    assert backfill_article(dynamodb_table, legacy, names, apply=True) == 'updated'
    saved = dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item']
    assert saved == {**legacy, 'category_name': 'Caching'}
    assert backfill_article(dynamodb_table, saved, {'caching': 'Renamed'}, apply=True) == 'skipped'
    assert dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item'] == saved
    with pytest.raises(ClientError) as conflict:
        backfill_article(dynamodb_table, legacy, names, apply=True)
    assert conflict.value.response['Error']['Code'] == 'ConditionalCheckFailedException'
    dynamodb_table.update_item(Key=key, UpdateExpression='REMOVE category_name')
    changed = patch(owner, f'/articles/{article_id}', json={'category': 'other'})
    assert changed.status_code == 200, changed.text
    with pytest.raises(ClientError):
        backfill_article(dynamodb_table, legacy, names, apply=True)
    current = dynamodb_table.get_item(Key=key, ConsistentRead=True)['Item']
    assert current['category'] == 'other'
    assert current['category_name'] == 'Other'
    with pytest.raises(ValueError, match='Unknown category'):
        backfill_article(dynamodb_table, {**key, 'category': 'missing'}, names, apply=True)
    assert json.loads(capsys.readouterr().out.splitlines()[0])['output']['applied'] is False


def test_article_provenance_is_validated_persisted_and_kept_out_of_public_content(guest_client):
    suffix = uuid.uuid4().hex[:10]
    identity = {'sub': 'provenance-' + suffix, 'iss': 'article-provenance-tests',
                'email': f'provenance-{suffix}@example.com', 'name': 'Provenance Creator'}
    owner = get_logged_in_client(identity)
    set_dynamodb_user_permissions(get_logged_in_user_id(identity), ['root'])
    source = {'url': f'https://medium.com/@example/original-{suffix}/', 'published_at': 1767225600000}
    generation = {'model': 'provenance-test-model', 'version': 'test-snapshot', 'reasoning_effort': 'high'}
    payload = {'title': 'Article provenance ' + suffix, 'content': ARTICLE_CONTENT,
               'category': 'distributed-systems', 'tags': ['provenance-test'],
               'source': source, 'generation': generation}
    for invalid in ({'source': {**source, 'published_at': True}},
                    {'source': {'url': 'javascript:alert(1)'}},
                    {'generation': {'model': 'test', 'reasoning': 'private analysis'}}):
        rejected = post(owner, '/articles', json={**payload, **invalid})
        assert rejected.status_code == 422, rejected.text

    created = post(owner, '/articles', json=payload)
    assert created.status_code == 200, created.text
    page = get(owner, created.json())
    assert page.status_code == 200, page.text
    status_path = urlsplit(pq(page.text)('#article-status-form').attr('action')).path
    article_id = status_path.split('/')[2]
    saved = get_dynamodb_article(article_id)
    assert saved['status'] == 'unpublished'
    assert saved['source'] == source and saved['generation'] == generation

    edited = patch(owner, f'/articles/{article_id}', json={'content': ARTICLE_CONTENT + '<p>Manual edit.</p>'})
    assert edited.status_code == 200, edited.text
    saved = get_dynamodb_article(article_id)
    assert saved['source'] == source and saved['generation'] == generation
    before_invalid = dict(saved)
    rejected = patch(owner, f'/articles/{article_id}', json={'source': {'url': 'https://example.com', 'extra': True}})
    assert rejected.status_code == 422, rejected.text
    assert get_dynamodb_article(article_id) == before_invalid

    published = post(owner, status_path, json={'status': 'published'})
    assert published.status_code == 200, published.text
    before = get_dynamodb_article(article_id)
    replacement = {'model': 'provenance-test-model-v2', 'reasoning_effort': 'medium'}
    edited = patch(owner, f'/articles/{article_id}', json={'generation': replacement})
    assert edited.status_code == 200, edited.text
    saved = get_dynamodb_article(article_id)
    assert saved['generation'] == replacement and saved['source'] == source
    for field in ('user_id', 'created_at', 'published_at', 'status', 'post_status_pk',
                  'post_category_status_pk', 'post_user_status_pk', 'rating_sk', 'content'):
        assert saved[field] == before[field]
    public = get(guest_client, created.json())
    assert public.status_code == 200, public.text
    assert source['url'] not in public.text and replacement['model'] not in public.text
    forbidden = patch(guest_client, f'/articles/{article_id}', json={'source': None, 'generation': None})
    assert forbidden.status_code in (401, 403), forbidden.text
    assert get_dynamodb_article(article_id) == saved

    cleared = patch(owner, f'/articles/{article_id}', json={'source': None, 'generation': None})
    assert cleared.status_code == 200, cleared.text
    saved = get_dynamodb_article(article_id)
    assert 'source' not in saved and 'generation' not in saved
