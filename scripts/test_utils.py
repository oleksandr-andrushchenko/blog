import uuid
from urllib.parse import quote
import os
from requests import Session, Response
import boto3
from botocore.config import Config
from utils import (
    logger,
    get_dynamodb_schema,
    encode_offset,
)

TEST_BASE_URL = os.getenv("TEST_BASE_URL")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT")
TEST_DYNAMODB_TABLE = os.getenv("TEST_DYNAMODB_TABLE")

aws_params = {
    "aws_access_key_id": "dummy",
    "aws_secret_access_key": "dummy",
    "endpoint_url": DYNAMODB_ENDPOINT,
    "config": Config(region_name=AWS_REGION),
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
    res = dynamodb_table.get_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": "META"
        }
    )
    return res["Item"]


def get_dynamodb_post(post_id: str) -> dict:
    res = dynamodb_table.get_item(
        Key={
            "pk": f"POST#{post_id}",
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


def get(client: Session, url: str) -> Response:
    return client.get(f"{TEST_BASE_URL}{url}")


def post(client: Session, url: str, json: dict) -> Response:
    return client.post(f"{TEST_BASE_URL}{url}", json=json)


def get_guest_client() -> Session:
    return Session()


def get_logged_in_client(user: dict) -> Session:
    fake_code = encode_offset(user)
    session = Session()
    resp = get(session, f"/auth/callback?redirect_url={quote(TEST_BASE_URL)}&code={fake_code}")
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
        "created_at_sk": 1760655417454,
        "user_status_pk": f"USER#STATUS#{status}",
    })
