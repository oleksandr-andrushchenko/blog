import os
import re
import uuid
from urllib.parse import quote, urlsplit

import boto3
from requests import Session, Response

from utils import (
    logger,
    get_dynamodb_schema,
    encode_offset,
)

WEB_TEST_BASE_URL = os.getenv("WEB_TEST_BASE_URL")
API_TEST_BASE_URL = os.getenv("API_TEST_BASE_URL")
TEST_BASE_URL = WEB_TEST_BASE_URL
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT")
TEST_DYNAMODB_TABLE = os.getenv("TEST_DYNAMODB_TABLE")
REQUEST_TIMEOUT = 10

aws_params = {
    "aws_access_key_id": "dummy",
    "aws_secret_access_key": "dummy",
    "endpoint_url": DYNAMODB_ENDPOINT,
    "region_name": AWS_REGION,
}

dynamodb = boto3.resource("dynamodb", **aws_params)
dynamodb_client = boto3.client("dynamodb", **aws_params)
dynamodb_table = dynamodb.Table(TEST_DYNAMODB_TABLE)

regular_user = {"sub": "regular-sub", "iss": "regular-iss", "email": "regular@example.com"}
regular_2_user = {"sub": "regular-2-sub", "iss": "regular-2-iss", "email": "regular2@example.com"}
root_user = {"sub": "root-sub", "iss": "root-iss", "email": "root@example.com"}


def set_dynamodb_user_permissions(user_id: str, permissions: list[str]) -> None:
    dynamodb_table.update_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": "META"
        },
        UpdateExpression="SET #permissions = :permissions",
        ExpressionAttributeNames={
            "#permissions": "permissions"
        },
        ExpressionAttributeValues={
            ":permissions": permissions
        }
    )


def get_dynamodb_user(user_id: str) -> dict:
    # todo: cache
    res = dynamodb_table.get_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": "META"
        }
    )
    return res["Item"]


def get_dynamodb_user_by_email(email: str) -> dict:
    res = dynamodb_table.query(
        IndexName="USERS_BY_EMAIL",
        KeyConditionExpression="user_email_pk = :email",
        ExpressionAttributeValues={":email": email},
        Limit=1,
    )
    return res["Items"][0]


def get_dynamodb_article(article_id: str) -> dict:
    # todo: cache
    res = dynamodb_table.get_item(
        Key={
            "pk": f"POST#{article_id}",
            "sk": "META"
        }
    )
    return res["Item"]


def recreate_dynamodb_table():
    schema = {**get_dynamodb_schema(), "TableName": TEST_DYNAMODB_TABLE}
    table_name = schema.get("TableName", TEST_DYNAMODB_TABLE)

    try:
        dynamodb_client.delete_table(TableName=table_name)
        dynamodb_client.get_waiter("table_not_exists").wait(TableName=table_name)
        logger.debug(f"🧹 Deleted old table: {table_name}")
    except dynamodb_client.exceptions.ResourceNotFoundException:
        pass

    create_params = {k: v for k, v in schema.items() if k != "TableName"}
    dynamodb.create_table(TableName=table_name, **create_params)
    dynamodb_client.get_waiter("table_exists").wait(TableName=table_name)
    logger.debug(f"🚀 Created table from schema: {table_name}")


def _api_path(path: str) -> str:
    if path.startswith("/posts"):
        return "/articles" + path[len("/posts"): ]
    if path.startswith("/post-"):
        return "/article-" + path[len("/post-"): ]
    if path.startswith("/post-tags"):
        return "/article-tags" + path[len("/post-tags"): ]
    return path


def _is_api_request(method: str, url: str) -> bool:
    path = _api_path(urlsplit(url).path)
    if method == "GET" and urlsplit(url).path == "/posts-fragment":
        return False
    if method == "GET":
        return path in {"/articles-fragment", "/article-tag-subscriptions", "/article-tags", "/users-fragment"} or any(
            path.endswith(suffix) for suffix in ("/comments-fragment", "/articles-fragment")
        )
    if method == "POST":
        return path in {
            "/public-file", "/articles", "/contacts/message",
            "/article-tag-subscriptions", "/dummy-fixtures",
            "/generate-sitemap", "/drop-cdn-cache",
        } or bool(re.fullmatch(r"/articles/[^/]+/(status|impression|comment)", path)) or bool(
            re.fullmatch(r"/users/[^/]+/(status|impression)", path)
        )
    if method == "PATCH":
        return path.startswith(("/articles/", "/users/", "/article-tags/"))
    return method == "DELETE" and path.startswith("/article-tag-subscriptions/")


def _copy_auth_cookies_to_api(client: Session) -> None:
    api_host = urlsplit(API_TEST_BASE_URL).hostname
    for cookie in list(client.cookies):
        client.cookies.set(cookie.name, cookie.value, domain=api_host, path=cookie.path or "/")


def _request(method: str, client: Session, url: str, **kwargs) -> Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    is_api = _is_api_request(method, url)
    if is_api:
        _copy_auth_cookies_to_api(client)
        cookie_header = "; ".join(f"{cookie.name}={cookie.value}" for cookie in client.cookies)
        if cookie_header:
            kwargs["headers"] = {**kwargs.get("headers", {}), "Cookie": cookie_header}
    base_url = API_TEST_BASE_URL if is_api else WEB_TEST_BASE_URL
    return getattr(client, method.lower())(f"{base_url}{url}", **kwargs)


def get(client: Session, url: str, **kwargs) -> Response:
    return _request("GET", client, url, **kwargs)


def post(client: Session, url: str, json: dict = None, **kwargs) -> Response:
    return _request("POST", client, url, json=json, **kwargs)


def patch(client: Session, url: str, json: dict = None, **kwargs) -> Response:
    return _request("PATCH", client, url, json=json, **kwargs)


def delete(client: Session, url: str, **kwargs) -> Response:
    return _request("DELETE", client, url, **kwargs)


def get_guest_client() -> Session:
    return Session()


def get_logged_in_client(user: dict) -> Session:
    fake_code = encode_offset(user)
    session = Session()
    resp = get(session, f"/login-callback?redirect_url={quote(TEST_BASE_URL)}&code={fake_code}")
    # print("Resp:",resp.content)
    # print("Cookies:", resp.cookies.get_dict())
    return session


def create_provider_user(
        dynamodb_table,
        user_id: str,
        iss: str = "iss",
        sub: str = "sub",
        email: str = "test@example.com"
):
    dynamodb_table.put_item(Item={
        "pk": f"PROVIDER_USER#{iss}#{sub}",
        "sk": "META",
        "user_id": user_id,
        "email": email,
        "created_at": 1760655417454,
        "updated_at": 1760655417454,
    })


def create_user(
        dynamodb_table,
        user_id: str,
        email: str,
        iss: str = "iss",
        sub: str = "sub",
        status: str = "active",
):
    if user_id is None:
        user_id = str(uuid.uuid4())
    dynamodb_table.put_item(Item={
        "pk": f"USER#{user_id}",
        "sk": "META",
        "id": user_id,
        "user_email_pk": email,
        "name": "John Doe",
        "providers": {iss: {"sub": sub}},
        "status": status,
        "rating_sk": -8239344582546,
        "created_at": 1760655417454,
        "user_status_pk": f"USER#STATUS#{status}",
    })
