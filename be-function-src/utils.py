import htmlmin
import re
import os
import boto3
import uuid
import datetime
import logging
import sys
import httpx
from jose import jwt, jwk
from jose.exceptions import JWTError
import base64
from typing import Callable, Optional, Dict, Any
from starlette.datastructures import State
from starlette.status import HTTP_200_OK
from jinja2 import Environment, FileSystemLoader, pass_context
from config import (
    is_prod,
    get_config,
    get_cognito_domain,
    get_aws_region,
    get_dynamodb_endpoint,
    get_dynamodb_table_name,
    get_base_url,
    get_contact_topic_arn,
    get_cognito_user_pool_id,
    get_cognito_client_id,
    get_cognito_client_secret,
)
from models import (
    MessageDTO,
    User,
    Token,
)
from errors import (
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    InvalidTokenKidError,
)


class Lazy:
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
    if is_prod():
        return
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


def get_html_content(template: str, data: Dict[str, Any]) -> str:
    if data is None:
        data = {}
    template = jinja2_env().get_template(template)
    html = template.render(data)
    return minify_html(html) if is_prod() else html


async def get_cognito_jwks() -> dict:
    async with httpx.AsyncClient() as client:
        jwks_url = f"https://cognito-idp.{get_aws_region()}.amazonaws.com/{get_cognito_user_pool_id()}/.well-known/jwks.json"
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()


async def configure_app_state(app_state: State) -> None:
    if is_prod():
        # Startup: fetch JWKS
        # Fetch JWKS keys for token validation
        app_state.jwks = await get_cognito_jwks()
    else:
        app_state.logged_in = False


async def get_user_from_token(token: Optional[str], app_state: State) -> Optional[User]:
    if not is_prod() and app_state.logged_in:
        return User(
            username="Test username",
            email="test@example.com",
            name="Test User",
            sub="test-sub"
        )

    if not token:
        return None

    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # Try to find key
        key = next((k for k in app_state.jwks.get("keys", []) if k["kid"] == kid), None)

        # If not found → refresh JWKS once
        if key is None:
            app_state.jwks = await get_cognito_jwks()
            key = next((k for k in app_state.jwks.get("keys", []) if k["kid"] == kid), None)

        if key is None:
            raise InvalidTokenKidError("Invalid token (unknown kid)")

        # Construct public key and decode JWT
        public_key = jwk.construct(key)
        claims = jwt.decode(token, public_key, algorithms=["RS256"], audience=get_cognito_client_id())

        return User(
            username=claims.get("cognito:username"),
            email=claims.get("email"),
            name=claims.get("name"),
            sub=claims.get("sub"),
        )
    except JWTError:
        raise InvalidTokenError("Invalid token")


async def serve_index(custom_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **get_config(),
        **custom_data
    }


async def serve_contacts(custom_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **get_config(),
        **custom_data
    }


async def serve_error(custom_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **get_config(),
        "auth": {
            "active": False
        },
        **custom_data
    }


async def serve_login(callback_url: str, non_prod_callback_url: str, referer: str, app_state: State) -> str:
    # todo: make sure referer belongs to the website
    if is_prod():
        redirect_url = (
            f"https://{get_cognito_domain()}/oauth2/authorize"
            f"?client_id={get_cognito_client_id()}"
            f"&response_type=code"
            f"&redirect_uri={referer if referer else callback_url}"
            f"&scope=openid+email+profile"
        )
    else:
        app_state.logged_in = True
        redirect_url = referer if referer else non_prod_callback_url
    return redirect_url


async def serve_logout(callback_url: str, non_prod_callback_url: str, referer: str, app_state: State) -> str:
    if is_prod():
        redirect_url = (
            f"https://{get_cognito_domain()}/logout"
            f"?client_id={get_cognito_client_id()}"
            f"&logout_uri={referer if referer else callback_url}"
        )
    else:
        app_state.logged_in = False
        redirect_url = referer if referer else non_prod_callback_url
    return redirect_url


async def serve_login_callback(code: str, redirect_url: str) -> Token:
    if not code:
        raise InvalidCodeError("Missing code")

    token_url = f"https://{get_cognito_domain()}/oauth2/token"
    cognito_client_id = get_cognito_client_id()
    cognito_client_secret = get_cognito_client_secret()
    data = {
        "grant_type": "authorization_code",
        "client_id": cognito_client_id,
        "code": code,
        "redirect_uri": redirect_url,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    # If client secret is used
    if cognito_client_secret:
        auth_str = f"{cognito_client_id}:{cognito_client_secret}"
        headers["Authorization"] = "Basic " + base64.b64encode(auth_str.encode()).decode()

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(token_url, data=data, headers=headers)
        if token_resp.status_code != HTTP_200_OK:
            raise CodeExchangeFailedError("Failed to exchange code")
        tokens = token_resp.json()

    # Store ID token in secure HTTP-only cookie
    # tokens = response from Cognito
    id_token = tokens["id_token"]
    if not id_token:
        raise InvalidTokenError("Missing id_token")

    # Decode without verification just to read 'exp'
    claims = jwt.get_unverified_claims(id_token)

    return Token(
        sub=claims.get("sub"),
        email=claims.get("email"),
        name=claims.get("name"),
        username=claims.get("cognito:username"),
        iat=claims.get("iat"),
        exp=claims.get("exp"),
        aud=claims.get("aud"),
        plain=id_token,
    )
