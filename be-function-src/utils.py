import htmlmin
import re
import os
import boto3
import aioboto3
import uuid
import datetime
import logging
import sys
import httpx
from urllib.parse import quote
from jose import jwt, jwk
from jose.exceptions import JWTError
import base64
from typing import Callable, Optional, Dict, Any, Union
from starlette.datastructures import State
from starlette.status import HTTP_200_OK
from jinja2 import Environment, FileSystemLoader, pass_context
import dotenv
import json
from datetime import datetime, timezone
from pydantic import BaseModel, EmailStr, Field


# -------------------------
# Models
# -------------------------

class UserToken(BaseModel):
    sub: str
    iss: str  # "cognito", "google", etc.
    email: Optional[str] = None
    name: Optional[str] = None
    username: Optional[str] = None  # only for Cognito native
    iat: Optional[datetime] = None  # issued at
    exp: Optional[datetime] = None  # expiration
    max_age: Optional[int] = None
    aud: Optional[Union[str, list[str]]] = None  # audience / client_id
    plain_token: Optional[str] = None  # plain token


class User(BaseModel):
    id: str  # internal app user ID
    email: Optional[str] = None
    name: Optional[str] = None
    username: Optional[str] = None
    providers: Dict[str, Dict[str, Optional[str]]] = Field(default_factory=dict)  # noqa
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MessageDTO(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    message: str = Field(..., min_length=5, max_length=1000)


# -------------------------
# Errors
# -------------------------

class BaseError(Exception):
    pass


class InvalidTokenError(BaseError):
    pass


class InvalidTokenKidError(BaseError):
    pass


class InvalidCodeError(BaseError):
    pass


class CodeExchangeFailedError(BaseError):
    pass


# -------------------------
# Config
# -------------------------

def get_live_config(load_env=False):
    if load_env:
        dotenv.load_dotenv(dotenv_path="/.env", override=True)

    return {
        "env": os.getenv("ENV"),
        "cloudfront_base_url": os.getenv("CLOUDFRONT_BASE_URL"),
        "aws_region": os.getenv("AWS_REGION"),
        "dynamodb_endpoint": os.getenv("DYNAMODB_ENDPOINT"),
        "dynamodb_table": os.getenv("DYNAMODB_TABLE"),
        "google_analytics_id": os.getenv("GOOGLE_ANALYTICS_ID"),
        "contact_topic_arn": os.getenv("CONTACT_TOPIC_ARN"),
        "allowed_origin": os.getenv("ALLOWED_ORIGIN"),
        "cognito_domain": os.getenv("COGNITO_DOMAIN"),  # e.g. myapp-auth.auth.us-east-1.amazoncognito.com
        "cognito_client_id": os.getenv("COGNITO_CLIENT_ID"),
        "cognito_client_secret": os.getenv("COGNITO_CLIENT_SECRET"),
        "cognito_user_pool_id": os.getenv("COGNITO_USER_POOL_ID"),
        **{
            "auth": {},
            "head": {},
            "header": {},
            "index": {},
            "contacts": {},
            "footer": {},
            "error": {}
        },
        **json.load(open("./data.json"))
    }


config = get_live_config()


def is_prod():
    return config.get("env") == "prod"


def get_config():
    if is_prod():
        return config
    return get_live_config(True)


def get_base_url():
    return get_config().get("base_url")


def get_aws_region():
    return get_config().get("aws_region")


def get_dynamodb_endpoint():
    return get_config().get("dynamodb_endpoint")


def get_dynamodb_table_name():
    return get_config().get("dynamodb_table")


def get_feature(feature):
    return get_config().get(feature)


def get_contact_topic_arn():
    return get_config().get("contact_topic_arn")


def get_allowed_origin():
    return get_config().get("allowed_origin")


def get_cognito_domain():
    return get_config().get("cognito_domain")


def get_cognito_client_id():
    return get_config().get("cognito_client_id")


def get_cognito_client_secret():
    return get_config().get("cognito_client_secret")


def get_cognito_user_pool_id():
    return get_config().get("cognito_user_pool_id")


# -------------------------
# Helpers
# -------------------------

class Lazy:
    def __init__(self, factory: Callable):
        self._factory = factory
        self._instance = None

    def __call__(self):
        if self._instance is None:
            self._instance = self._factory()
        return self._instance


def get_logger():
    lg = logging.getLogger("app")
    lg.setLevel(logging.INFO if is_prod() else logging.DEBUG)
    if not lg.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
        )
        handler.setFormatter(formatter)
        lg.addHandler(handler)
    return lg


logger = get_logger()


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


def get_aioboto3_session():
    # logger.debug(f"aws_region: {get_aws_region()}")
    args = {} if is_prod() else {
        "aws_access_key_id": "dummy",
        "aws_secret_access_key": "dummy",
        "region_name": get_aws_region(),
    }
    return aioboto3.Session(**args)


aioboto3_session = Lazy(get_aioboto3_session)


def deserialize_item(item: dict) -> dict:
    """
    Convert DynamoDB AttributeValue dict to plain Python dict.
    e.g., {'S': 'string'} -> 'string', {'N': '42'} -> 42
    """
    result = {}
    for k, v in item.items():
        if "S" in v:
            result[k] = v["S"]
        elif "N" in v:
            result[k] = int(v["N"]) if "." not in v["N"] else float(v["N"])
        elif "BOOL" in v:
            result[k] = v["BOOL"]
        elif "M" in v:
            result[k] = deserialize_item(v["M"])
        elif "L" in v:
            result[k] = [deserialize_item(x["M"]) if "M" in x else list(x.values())[0] for x in v["L"]]
        else:
            result[k] = None
    return result


async def with_dynamodb_table(fn: Callable):
    """
    Opens a DynamoDB client and runs async function `fn(client)`.
    """
    session = aioboto3_session()
    # logger.debug(f"dynamodb_endpoint: {get_dynamodb_endpoint()}")
    async with session.client("dynamodb", endpoint_url=get_dynamodb_endpoint()) as client:
        return await fn(client)


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


def to_datetime(ts: Any) -> Optional[datetime]:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


async def get_user_by_user_token(token: UserToken) -> Optional[User]:
    async def fn(client):
        table_name = get_dynamodb_table_name()
        provider_item = None
        internal_item = None
        user_id = None

        # 1: Lookup provider record by GSI_PROVIDER_SUB
        if token.sub:
            query_value = f"{token.iss}#{token.sub}"
            logger.debug(f"Querying GSI_PROVIDER_SUB with value: {query_value}")

            resp = await client.query(
                TableName=table_name,
                IndexName="GSI_PROVIDER_SUB",
                KeyConditionExpression="gsi_provider_sub = :ps",
                ExpressionAttributeValues={":ps": {"S": query_value}}
            )
            items = resp.get("Items", [])
            if items:
                provider_item = deserialize_item(items[0])
                user_id = provider_item["user_id"]

                # Fetch internal record
                resp2 = await client.get_item(
                    TableName=table_name,
                    Key={"pk": {"S": f"USER#{user_id}"}, "sk": {"S": "INTERNAL"}}
                )
                internal_item = deserialize_item(resp2.get("Item")) if resp2.get("Item") else None

        # 2: Fallback: lookup internal user by GSI_EMAIL
        if not provider_item and token.email:
            resp = await client.query(
                TableName=table_name,
                IndexName="GSI_EMAIL",
                KeyConditionExpression="gsi_email = :email",
                ExpressionAttributeValues={":email": {"S": token.email}}
            )
            items = resp.get("Items", [])
            if items:
                internal_item = deserialize_item(items[0])
                user_id = internal_item["id"]

        # 3: Not found
        if not internal_item:
            return None

        # Ensure providers field is a dict
        if isinstance(internal_item.get("providers"), str):
            try:
                internal_item["providers"] = json.loads(internal_item["providers"])
            except json.JSONDecodeError:
                internal_item["providers"] = {}

        return User(
            id=user_id,
            email=internal_item.get("gsi_email", token.email),
            name=internal_item.get("name", token.name),
            username=internal_item.get("username", token.username),
            providers=internal_item.get("providers", {}),
            created_at=internal_item.get("created_at"),
            updated_at=internal_item.get("updated_at")
        )

    return await with_dynamodb_table(fn)


async def upsert_user_by_user_token(token: UserToken) -> User:
    async def fn(client):
        table_name = get_dynamodb_table_name()
        now = datetime.now(timezone.utc).isoformat()

        # 1: Lookup existing user
        existing_user = await get_user_by_user_token(token)
        if existing_user:
            user_id = existing_user.id
            providers = existing_user.providers
        else:
            user_id = str(uuid.uuid4())
            providers = {}

        # 2: Merge or add provider info
        providers[token.iss] = {"sub": token.sub, "username": token.username, "name": token.name}

        # 3: Ensure internal record exists
        internal_item = {
            "pk": f"USER#{user_id}",
            "sk": "INTERNAL",
            "id": user_id,
            "gsi_email": token.email,
            "name": token.name,
            "username": token.username,
            "providers": json.dumps(providers),
            "created_at": now,
            "updated_at": now
        }
        await client.put_item(TableName=table_name, Item={k: {"S": str(v)} for k, v in internal_item.items()})

        # 4: Ensure provider record exists
        provider_item = {
            "pk": f"USER#{token.iss}#{token.sub}",
            "sk": "PROFILE",
            "user_id": user_id,
            "gsi_provider_sub": f"{token.iss}#{token.sub}",
            "email": token.email,
            "created_at": now,
            "updated_at": now
        }
        await client.put_item(TableName=table_name, Item={k: {"S": str(v)} for k, v in provider_item.items()})

        # 5: Return User model
        return User(
            id=user_id,
            email=token.email,
            name=token.name,
            username=token.username,
            providers=providers,
            created_at=internal_item["created_at"],
            updated_at=internal_item["updated_at"]
        )

    return await with_dynamodb_table(fn)


def map_jwt_claims_to_user_token(claims: dict[str, Any], plain_token: str = None) -> UserToken:
    exp = to_datetime(claims.get("exp"))
    max_age = None

    if exp is not None:
        now = datetime.now(timezone.utc)
        delta = exp - now
        max_age = max(0, int(delta.total_seconds()))

    return UserToken(
        sub=claims["sub"],  # required
        iss=claims.get("iss"),
        email=claims.get("email"),
        name=claims.get("name"),
        username=claims.get("cognito:username"),
        iat=to_datetime(claims.get("iat")),
        exp=exp,
        max_age=max_age,
        aud=claims.get("aud"),
        plain_token=plain_token,
    )


def get_dummy_user_token() -> UserToken:
    return UserToken(
        sub="test-sub",
        iss="test-iss",
        username="Test username",
        email="test@example.com",
        name="Test User",
        iat=None,
        exp=None,
        max_age=None,
        aud="",
        plain_token="dummy"
    )


async def get_user_token_by_plain_token(plain_token: Optional[str], app_state: State) -> Optional[UserToken]:
    if not plain_token:
        return None

    if not is_prod():
        return get_dummy_user_token()

    try:
        unverified_header = jwt.get_unverified_header(plain_token)
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
        claims = jwt.decode(plain_token, public_key, algorithms=["RS256"], audience=get_cognito_client_id())

        return map_jwt_claims_to_user_token(claims)
    except JWTError:
        raise InvalidTokenError("Invalid token")


async def get_user_by_plain_token(plain_token: Optional[str], app_state: State) -> Optional[User]:
    user_token = await get_user_token_by_plain_token(plain_token, app_state)

    if user_token is None:
        return None

    # logger.debug(f"user_token: {user_token}")

    user = await get_user_by_user_token(user_token)
    # logger.debug(f"user: {user}")

    return user


# -------------------------
# Services
# -------------------------

# --- Post functions ---
async def create_post(title: str, slug: str, author_id: str, content: str, tags: list[str]):
    async def fn(table):
        post_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Post metadata
        await table.put_item(
            Item={
                "pk": f"POST#{post_id}",
                "sk": "METADATA",
                "post_id": post_id,
                "title": title,
                "slug": slug,
                "author_id": author_id,
                "content": content,
                "tags": tags,
                "created_at": now,
                "updated_at": now
            },
            ConditionExpression="attribute_not_exists(PK)"
        )

        # Tag references
        for tag in tags:
            await table.put_item(
                Item={
                    "pk": f"TAG#{tag}",
                    "sk": f"POST#{post_id}",
                    "post_id": post_id,
                    "title": title,
                    "slug": slug,
                    "created_at": now
                }
            )

        return {"post_id": post_id}

    return await with_dynamodb_table(fn)


async def get_post(post_id: str):
    async def fn(table):
        resp = table.get_item(Key={"pk": f"POST#{post_id}", "sk": "METADATA"})
        return resp.get("Item")

    return await with_dynamodb_table(fn)


async def list_posts(limit: int = 10):
    async def fn(table):
        resp = table.scan(Limit=limit)
        return resp.get("Items", [])

    return await with_dynamodb_table(fn)


async def serve_create_contacts_message(message: MessageDTO) -> None:
    if not is_prod():
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


async def serve_login(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/oauth2/authorize"
            f"?client_id={get_cognito_client_id()}"
            f"&response_type=code"
            f"&redirect_uri={quote(callback_url, safe='')}"
            f"&scope=openid+email+profile"
        )

    return callback_url


async def serve_login_callback(code: str, callback_url: str) -> UserToken:
    if is_prod():
        if not code:
            raise InvalidCodeError("Missing code")

        token_url = f"https://{get_cognito_domain()}/oauth2/token"
        cognito_client_id = get_cognito_client_id()
        cognito_client_secret = get_cognito_client_secret()
        data = {
            "grant_type": "authorization_code",
            "client_id": cognito_client_id,
            "code": code,
            "redirect_uri": quote(callback_url, safe=''),
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + base64.b64encode(
                f"{cognito_client_id}:{cognito_client_secret}".encode()
            ).decode()
        }

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(token_url, data=data, headers=headers)
            if token_resp.status_code != HTTP_200_OK:
                raise CodeExchangeFailedError("Failed to exchange code")
            tokens = token_resp.json()

        token = tokens.get("id_token")
        if not token:
            raise InvalidTokenError("Missing token")

        claims = jwt.get_unverified_claims(token)
        user_token = map_jwt_claims_to_user_token(claims, token)
    else:
        user_token = get_dummy_user_token()

    await upsert_user_by_user_token(user_token)
    return user_token


async def serve_logout(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/logout"
            f"?client_id={get_cognito_client_id()}"
            f"&logout_uri={quote(callback_url, safe='')}"
        )

    return callback_url
