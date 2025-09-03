import htmlmin
import re
import os
from config import is_prod, get_aws_region, get_dynamodb_endpoint, get_dynamodb_table_name, get_base_url, \
    get_contact_topic_arn, get_config, get_cognito_user_pool_id
from jinja2 import Environment, FileSystemLoader
from jinja2 import pass_context
import boto3
import uuid
import datetime
from models import MessageDTO, User
from typing import Callable
import logging
import sys
import httpx


class Lazy:
    """
    Lazy loader wrapper. Only initializes the resource when first accessed.
    """

    def __init__(self, factory: Callable):
        self._factory = factory
        self._instance = None

    def get(self):
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def __call__(self):
        return self.get()


def get_logger():
    lg = logging.getLogger("app")
    lg.setLevel(logging.INFO)
    if not lg.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
        )
        handler.setFormatter(formatter)
        lg.addHandler(handler)
    return lg


logger = Lazy(get_logger)


@pass_context
def url_for(ctx, name: str, **params) -> str:
    request = ctx.get("request")
    return get_url(request, name, **params)


def get_url(request, name: str, **params) -> str:
    url = request.url_for(name, **params) if request else f"/{name}"
    return str(url).rstrip("/")


@pass_context
def full_url_for(ctx, name: str, **params) -> str:
    request = ctx.get("request")
    return get_full_url(request, name, **params)


def get_full_url(request, name: str, **params) -> str:
    url = request.url_for(name, **params) if request else f"/{name}"
    return str(url).rstrip("/")


def asset_url(path: str, with_base: bool = False) -> str:
    path = path.lstrip("/")
    base = get_base_url() if with_base else ""
    return f"{base}/assets/{path}"


def get_jinja2_env():
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    jinja2_env = Environment(
        loader=FileSystemLoader(templates_dir),
        auto_reload=not is_prod()
    )
    jinja2_env.globals.update({
        "asset_url": asset_url,
        "url": url_for,
        "full_url": full_url_for
    })
    return jinja2_env


jinja2_env = Lazy(get_jinja2_env)


def minify_html(html: str) -> str:
    # Step 1: Minify using htmlmin
    html = htmlmin.minify(
        html,
        remove_comments=True,
        remove_empty_space=True,
        remove_all_empty_space=True,
        reduce_empty_attributes=True,
        reduce_boolean_attributes=True,
        remove_optional_attribute_quotes=True,
        keep_pre=False
    )

    # Step 2: Normalize attribute values — collapse inner whitespace and strip leading/trailing
    def clean_attr_value(match):
        attr = match.group(1)
        quote = match.group(2)
        value = match.group(3)
        cleaned = re.sub(r'\s+', ' ', value).strip()
        return f'{attr}={quote}{cleaned}{quote}'

    # This handles key="value with   spaces\nand lines"
    html = re.sub(r'(\w+)=([\'"])(.*?)\2', clean_attr_value, html, flags=re.DOTALL)

    return html.strip()


def get_dynamodb_client():
    if is_prod():
        return boto3.client("dynamodb", region_name=get_aws_region())
    return boto3.client(
        "dynamodb",
        region_name=get_aws_region(),
        endpoint_url=get_dynamodb_endpoint()
    )


def get_dynamodb_table(table_name: str):
    return boto3.resource(
        "dynamodb",
        region_name=get_aws_region(),
        endpoint_url=None if is_prod() else get_dynamodb_endpoint()
    ).Table(table_name)


dynamodb_table = Lazy(lambda: get_dynamodb_table(get_dynamodb_table_name()))


# --- Post functions ---
def create_post(title: str, slug: str, author_id: str, content: str, tags: list[str]):
    post_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow().isoformat()

    # Post metadata
    dynamodb_table().put_item(Item={
        "PK": f"POST#{post_id}",
        "SK": "METADATA",
        "post_id": post_id,
        "title": title,
        "slug": slug,
        "author_id": author_id,
        "content": content,
        "tags": tags,
        "created_at": now
    })

    # Tag references
    for tag in tags:
        dynamodb_table().put_item(Item={
            "PK": f"TAG#{tag}",
            "SK": f"POST#{post_id}",
            "post_id": post_id,
            "title": title,
            "slug": slug,
            "created_at": now
        })

    return {"post_id": post_id}


def get_post(post_id: str):
    resp = dynamodb_table().get_item(Key={"PK": f"POST#{post_id}", "SK": "METADATA"})
    return resp.get("Item")


def list_posts(limit: int = 10):
    resp = dynamodb_table().scan(Limit=limit)
    return resp.get("Items", [])


def serve_create_message(message: MessageDTO) -> None:
    sns_client = boto3.client("sns", region_name=get_aws_region())

    text = (
        f"New contact form submission:\n"
        f"Name: {message.name}\n"
        f"Email: {message.email}\n"
        f"Message: {message.message}"
    )

    sns_client.publish(
        TopicArn=get_contact_topic_arn(),
        Message=text,
        Subject="New Contact Form Submission"
    )


def get_html_content(template: str, request, current_user: User = None, data: dict = None) -> str:
    if data is None:
        data = {}
    template = jinja2_env().get_template(template)
    html = template.render({
        **get_config(),
        **data,
        "request": request,
        "current_user": current_user
    })
    return minify_html(html) if is_prod() else html


async def get_cognito_jwks() -> dict:
    async with httpx.AsyncClient() as client:
        jwks_url = f"https://cognito-idp.{get_aws_region()}.amazonaws.com/{get_cognito_user_pool_id()}/.well-known/jwks.json"
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()
