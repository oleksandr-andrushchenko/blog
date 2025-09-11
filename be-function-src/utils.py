import htmlmin
import re
import os
import aioboto3
import uuid
import datetime
import logging
import sys
import httpx
from enum import Enum
from urllib.parse import quote
from jose import jwt
from jose.exceptions import JWTError
import base64
from typing import Callable, Optional, Dict, Any, Union, List
from starlette.datastructures import State
from starlette.status import HTTP_200_OK
from jinja2 import Environment, FileSystemLoader, pass_context
import dotenv
import json
from datetime import datetime, timezone
from pydantic import BaseModel, EmailStr, Field, field_validator, conlist, computed_field
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from itertools import combinations


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
    aud: Optional[Union[str, List[str]]] = None  # audience / client_id
    plain_token: Optional[str] = None  # plain token


class User(BaseModel):
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    username: Optional[str] = None
    providers: Dict[str, Dict[str, Optional[str]]] = Field(default_factory=dict)  # noqa
    permissions: List[str] = Field(default_factory=lambda: [Permission.REGULAR])  # noqa
    created_at: str
    updated_at: Optional[str] = None


class ContactMessageDTO(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    message: str = Field(..., min_length=5, max_length=1000)


class ContactMessage(BaseModel):
    id: str
    name: str
    email: str
    message: str
    user_id: Optional[str] = None
    created_at: str


class PostDTO(BaseModel):
    title: str
    slug: Optional[str] = None
    content: str
    tags: conlist(str, min_length=1, max_length=3)

    @field_validator("slug", mode="before")
    @classmethod
    def build_slug_if_missing(cls, value, info):
        if value:
            return to_kebab_case(value)
        # get title from other fields
        title = info.data.get("title") if info.data else ""
        return to_kebab_case(title) if title else None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        if not value:
            return []
        # lowercase, kebab-case, dedupe
        normalized = [to_kebab_case(t) for t in value]
        return list(dict.fromkeys(normalized))


class TagQueryDTO(BaseModel):
    prefix: Optional[str] = Field(None, min_length=2, max_length=100)
    limit: int = Field(default=10, ge=1)


class Tag(BaseModel):
    name: str
    posts_count: Optional[int] = None


class PublicTag(BaseModel):
    name: str


class PostStatus(str, Enum):
    UNPUBLISHED = "unpublished"
    PUBLISHED = "published"
    REJECTED = "rejected"


class Post(BaseModel):
    id: str
    title: str
    slug: str
    user_id: str
    content: str
    tags: List[Tag]
    status: PostStatus = PostStatus.UNPUBLISHED
    created_at: str
    updated_at: Optional[str] = None


class PublicPost(BaseModel):
    id: str
    slug: str

    @computed_field
    def url(self) -> str:
        return get_url()


class PublicContactMessage(BaseModel):
    id: str


class Permission(str, Enum):
    REGULAR = "regular"
    ROOT = "root"
    ALL = "*"

    CREATE_POST = "create_post"
    APPROVE_POST = "approve_post"
    CREATE_CONTACT_MESSAGE = "create_contact_message"


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


class DynamoDBTransactionError(BaseError):
    def is_conditional(self) -> bool:
        return "ConditionalCheckFailed" in str(self)


class SlugDuplicationError(BaseError):
    pass


class PostNotFound(BaseError):
    pass


class UserNotFound(BaseError):
    pass


class AuthorizationFailedError(BaseError):
    def __init__(self, permission: str):
        self.permission = permission
        super().__init__(f"User lacks required permission: {permission}")


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
        "permission_hierarchy": {
            Permission.REGULAR: [
                Permission.CREATE_POST,
                Permission.CREATE_CONTACT_MESSAGE,
            ],
            Permission.ROOT: [
                Permission.ALL
            ],
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


def get_permission_hierarchy() -> Dict[str, List[str]]:
    return get_config().get("permission_hierarchy")


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


def verify_authorization(
        user: User,
        permission: str,
        resource: BaseModel = None,
        permissions: Optional[List[str]] = None,
        hierarchy: Optional[Dict[str, List[str]]] = None,
) -> bool:
    """
    Verify if user has access to perform action requiring `permission`.
    """
    hierarchy = hierarchy or get_permission_hierarchy()

    # Owner check
    if resource:
        data = resource.model_dump()
        owner_id = data.get("owner_id") or data.get("user_id")
        if owner_id and str(owner_id) == str(user.id):
            return True

    # Default to user permissions
    permissions = permissions or user.permissions or [Permission.REGULAR]

    # Root/all permissions
    if Permission.ALL in permissions:
        return True

    if permission in permissions:
        return True

    # Check inherited permissions
    for user_permission in permissions:
        children = hierarchy.get(user_permission, [])
        if children:
            if verify_authorization(user, permission, resource, children, hierarchy):
                return True

    # No match → fail
    raise AuthorizationFailedError(permission)


def to_kebab_case(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def dynamodb_transact_write_or_raise(table, transact_items: List[Dict[str, Any]]):
    """
    Executes a DynamoDB TransactWriteItems call and raises a
    DynamoTransactionError with detailed reasons if it fails.
    """
    try:
        await table.meta.client.transact_write_items(TransactItems=transact_items)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "TransactionCanceledException":
            details = []
            reasons = e.response.get("CancellationReasons", [])

            for reason in reasons:
                if not reason or not isinstance(reason, dict):
                    continue

                code = reason.get("Code")
                if not code or code == "None":
                    continue

                msg = reason.get("Message")
                if not msg:
                    continue

                details.append(f"{code} - {msg}")

            if details:
                details_text = " (" + ". ".join(details) + ")"
            else:
                details_text = ""

            raise DynamoDBTransactionError(f"DynamoDB transaction failed{details_text}")
        raise


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


def static_url(path: str, with_base: bool = False) -> str:
    path = path.lstrip("/")
    base = get_base_url() if with_base else ""
    return f"{base}/static/{path}"


def get_jinja2_env():
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    jinja2_env = Environment(
        loader=FileSystemLoader(templates_dir),
        auto_reload=not is_prod()
    )
    jinja2_env.globals.update({
        "static_url": static_url,
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


def get_dynamodb_resource_kwargs():
    return {} if is_prod() else {
        "aws_access_key_id": "dummy",
        "aws_secret_access_key": "dummy",
        "region_name": get_aws_region(),
        "endpoint_url": get_dynamodb_endpoint(),
    }


aioboto3_session = Lazy(get_aioboto3_session)


async def with_dynamodb_table(fn: Callable):
    session = aioboto3_session()
    async with session.resource("dynamodb", **get_dynamodb_resource_kwargs()) as dynamodb:
        table = await dynamodb.Table(get_dynamodb_table_name())
        return await fn(table)


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
    async def fn(table):
        provider_item = None
        internal_item = None
        user_id = None

        # 1: Lookup provider record by GSI_PROVIDER_SUB
        if token.sub:
            provider_sub = f"{token.iss}#{token.sub}"
            logger.debug(f"Querying GSI_PROVIDER_SUB with value: {provider_sub}")

            resp = await table.query(
                IndexName="GSI_PROVIDER_SUB",
                KeyConditionExpression=Key("gsi_provider_sub").eq(provider_sub)
            )
            items = resp.get("Items", [])
            if items:
                provider_item = items[0]
                user_id = provider_item["user_id"]

                # Fetch internal record
                resp2 = await table.get_item(
                    Key={
                        "pk": f"USER#{user_id}",
                        "sk": "INTERNAL"
                    }
                )
                internal_item = resp2.get("Item") if "Item" in resp2 else None

        # 2: Fallback: lookup internal user by GSI_EMAIL
        if not provider_item and token.email:
            resp = await table.query(
                IndexName="GSI_EMAIL",
                KeyConditionExpression=Key("gsi_email").eq(token.email)
            )
            items = resp.get("Items", [])
            if items:
                internal_item = items[0]
                user_id = internal_item["id"]

        # 3: Not found
        if not internal_item:
            return None

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
    async def fn(table):
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
            "providers": providers,
            "created_at": now,
            "updated_at": now
        }
        await table.put_item(Item=internal_item)

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
        await table.put_item(Item=provider_item)

        # 5: Return User model
        return User(
            id=user_id,
            email=token.email,
            name=token.name,
            username=token.username,
            providers=providers,
            created_at=internal_item.get("created_at"),
            updated_at=internal_item.get("updated_at")
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
        sub=claims.get("sub"),
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


async def create_dummy_fixtures() -> None:
    if is_prod():
        return
    user_token = get_dummy_user_token()
    user = await upsert_user_by_user_token(user_token)
    posts = [
        PostDTO(
            title="Post title #111111111111111111111111",
            slug="post-title-111111111111111111111111",
            content="Post content #111111111111111111111111" * 100,
            tags=["tag1", "tag2", "tag3"]
        ),
        PostDTO(
            title="Post title #22222222222222222222222",
            slug="post-title-22222222222222222222222",
            content="Post content #2222222222222222222222" * 100,
            tags=["tag2", "tag3"]
        ),
        PostDTO(
            title="Post title #3333333333333333333333333",
            slug="post-title-3333333333333333333333333",
            content="Post content #333333333333333333333" * 100,
            tags=["tag1", "tag3"]
        ),
    ]
    for post in posts:
        await create_post(post, user, status=PostStatus.PUBLISHED)


async def get_user_token_by_plain_token(plain_token: Optional[str], app_state: State) -> Optional[UserToken]:
    if not plain_token:
        return None
    if not is_prod():
        return get_dummy_user_token()
    try:
        unverified_header = jwt.get_unverified_header(plain_token)
        kid = unverified_header.get("kid")

        key = next((k for k in app_state.jwks.get("keys", []) if k["kid"] == kid), None)
        if key is None:
            app_state.jwks = await get_cognito_jwks()
            key = next((k for k in app_state.jwks.get("keys", []) if k["kid"] == kid), None)
        if key is None:
            raise InvalidTokenKidError("Invalid token (unknown kid)")

        issuer = f"https://cognito-idp.{get_aws_region()}.amazonaws.com/{get_cognito_user_pool_id()}"
        claims = jwt.decode(
            plain_token,
            key,  # pass the JWK dict
            algorithms=["RS256"],
            audience=get_cognito_client_id(),
            issuer=issuer,
        )
        return map_jwt_claims_to_user_token(claims, plain_token)
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

async def get_create_post_page_data(**kwargs: Any) -> Dict[str, Any]:
    data = {
        **get_config(),
        **kwargs
    }

    request = data.get("request")
    data.update({
        "breadcrumbs": {
            data.get("index_breadcrumb", "Home"): get_url(request=request, name="index"),
            data.get("create_post_breadcrumb", "Create post"): None,
        }
    })

    return data


# -------------------------
# Create Post with tags metadata and posts_count increment
# -------------------------
async def create_post(post_dto: PostDTO, user: User, status=PostStatus.UNPUBLISHED) -> Post:
    verify_authorization(user, Permission.CREATE_POST)

    async def fn(table):
        now = utc_now_iso()
        post_id = str(uuid.uuid4())

        # Main post item
        post_item = {
            "pk": f"POST#{post_id}",
            "sk": "METADATA",
            "post_id": post_id,
            "title": post_dto.title,
            "slug": post_dto.slug,
            "user_id": user.id,
            "content": post_dto.content,
            "tags": list(dict.fromkeys(post_dto.tags)),
            "status": status,
            "created_at": now,
            "gsi_post_pk": "POST",
            "gsi_status_created_at": f"STATUS#{status.value}#CREATED_AT#{now}",
        }

        # Slug item for uniqueness
        slug_item = {
            "pk": f"SLUG#{post_dto.slug}",
            "sk": "POST",
            "post_id": post_id,
            "created_at": now,
        }

        transact_items = [
            {
                "Put": {
                    "TableName": table.name,
                    "Item": slug_item,
                    "ConditionExpression": "attribute_not_exists(pk)"
                }
            },
            {
                "Put": {
                    "TableName": table.name,
                    "Item": post_item,
                    "ConditionExpression": "attribute_not_exists(pk)"
                }
            }
        ]

        # ----------------------------
        # Tag metadata (increment posts_count)
        # ----------------------------
        for tag in post_item["tags"]:
            tag_pk = f"TAG#{tag}"
            transact_items.append({
                "Update": {
                    "TableName": table.name,
                    "Key": {"pk": tag_pk, "sk": "METADATA"},
                    "UpdateExpression": (
                        "SET tag_name = :tag, "
                        "created_at = if_not_exists(created_at, :now), "
                        "posts_count = if_not_exists(posts_count, :zero) + :inc, "
                        "gsi_tag_pk = :gsi_tag_pk, "
                        "gsi_tag_name_pk = :gsi_tag_pk"
                    ),
                    "ExpressionAttributeValues": {
                        ":tag": tag,
                        ":now": now,
                        ":inc": 1,
                        ":zero": 0,
                        ":gsi_tag_pk": "TAG"
                    }
                }
            })

        # Execute transaction
        try:
            await dynamodb_transact_write_or_raise(table, transact_items)
        except DynamoDBTransactionError as e:
            if e.is_conditional():
                raise SlugDuplicationError("Duplicate slug")
            raise

        return Post(
            id=post_id,
            title=post_item["title"],
            slug=post_item["slug"],
            user_id=post_item["user_id"],
            content=post_item["content"],
            tags=[Tag(name=t) for t in post_item["tags"]],
            created_at=now,
            updated_at=now,
        )

    return await with_dynamodb_table(fn)


async def find_post(post_id: str) -> Optional[Post]:
    async def fn(table):
        resp = await table.get_item(
            Key={
                "pk": f"POST#{post_id}",
                "sk": "METADATA"
            }
        )
        item = resp.get("Item")
        if not item:
            return None

        return Post(
            id=item["post_id"],
            title=item["title"],
            slug=item["slug"],
            user_id=item["user_id"],
            content=item["content"],
            tags=[Tag(name=t) for t in item.get("tags", [])],
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
        )

    return await with_dynamodb_table(fn)


async def get_post(post_id: str) -> Post:
    post = await find_post(post_id)
    if post is None:
        raise PostNotFound(f"Post '{post_id}' not found")
    return post


async def find_user(user_id: str) -> Optional[User]:
    async def fn(table):
        resp = await table.get_item(
            Key={
                "pk": f"USER#{user_id}",
                "sk": "INTERNAL"
            }
        )
        item = resp.get("Item")
        if not item:
            return None
        # logger.debug(f"User: {item}")
        return User(
            id=user_id,
            email=item.get("gsi_email"),
            name=item.get("name"),
            username=item.get("username"),
            providers=item.get("providers", {}),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at")
        )

    return await with_dynamodb_table(fn)


async def get_user(user_id: str) -> User:
    user = await find_user(user_id)
    if user is None:
        raise UserNotFound(f"User '{user_id}' not found")
    return user


async def get_latest_posts(limit: int = 10, last_sk: Optional[str] = None) -> List[Post]:
    """
    Fetch latest posts.
    Only returns published posts.
    """

    async def fn(table):
        status = PostStatus.PUBLISHED
        key_cond = Key("gsi_post_pk").eq("POST") & Key("gsi_status_created_at").begins_with(
            f"STATUS#{status.value}#")
        if last_sk:
            key_cond &= Key("gsi_status_created_at").lt(last_sk)

        resp = await table.query(
            IndexName="GSI_POST_STATUS_CREATED_AT",
            KeyConditionExpression=key_cond,
            ScanIndexForward=False,
            Limit=limit
        )
        items = resp.get("Items", [])
        # logger.debug(json.dumps(items,indent=4))
        return [
            Post(
                id=item["post_id"],
                title=item["title"],
                slug=item["slug"],
                user_id=item.get("user_id"),
                content=item.get("content"),
                tags=[Tag(name=t) for t in item.get("tags", [])],
                status=item.get("status", status),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
            )
            for item in items
        ]

    return await with_dynamodb_table(fn)


# -------------------------
# Latest Posts by Tag (cursor-based)
# -------------------------
async def get_latest_posts_by_tags(
        tags: List[str],
        limit: int = 10,
        last_sk: Optional[str] = None
) -> List[Post]:
    """
    Fetch latest posts that match all given tags (AND).
    Only returns published posts.
    """

    async def fn(table):
        combo_pk = "TAG_COMBO#" + "#".join(sorted(tags))
        key_cond = Key("pk").eq(combo_pk)
        if last_sk:
            key_cond &= Key("sk").lt(last_sk)

        # Query combo-key table to get post IDs
        resp = await table.query(
            KeyConditionExpression=key_cond,
            ScanIndexForward=False,
            Limit=limit
        )
        combo_items = resp.get("Items", [])
        if not combo_items:
            return []

        post_ids = [item["post_id"] for item in combo_items]
        keys = [{"pk": f"POST#{pid}", "sk": "METADATA"} for pid in post_ids]

        # Batch get post metadata
        resp = await table.batch_get_item(RequestItems={table.name: {"Keys": keys}})
        post_items = resp["Responses"].get(table.name, [])

        # Filter out unpublished posts
        post_items = [item for item in post_items if item.get("status") == PostStatus.PUBLISHED]

        # Maintain original order
        post_items_map = {item["post_id"]: item for item in post_items}
        ordered_posts = [post_items_map[pid] for pid in post_ids if pid in post_items_map]

        return [
            Post(
                id=item["post_id"],
                title=item["title"],
                slug=item["slug"],
                user_id=item.get("user_id"),
                content=item.get("content"),
                tags=[Tag(name=t) for t in item.get("tags", [])],
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
            )
            for item in ordered_posts
        ]

    return await with_dynamodb_table(fn)


async def approve_post(post: Post, user: User) -> None:
    verify_authorization(user, Permission.APPROVE_POST)

    async def fn(table):
        now = utc_now_iso()
        status = PostStatus.PUBLISHED

        # 1. Update main post
        await table.update_item(
            Key={
                "pk": f"POST#{post.id}",
                "sk": "METADATA"
            },
            UpdateExpression="SET #status = :published, "
                             "updated_at = if_not_exists(updated_at, :now), "
                             "gsi_status_created_at = :gsi_val",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":published": status,
                ":now": now,
                ":gsi_val": f"STATUS#{status.value}#CREATED_AT#{now}"
            }
        )

        # 2. Generate tag combos and create combo items
        transact_items = []
        tags = post.tags or []
        title = post.title
        slug = post.slug
        post_id = post.id

        for r in range(1, len(tags) + 1):
            for combo in combinations(sorted([t.name for t in tags]), r):
                combo_pk = "TAG_COMBO#" + "#".join(combo)
                combo_sk = f"CREATED_AT#{now}#POST#{post_id}"
                transact_items.append({
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            "pk": combo_pk,
                            "sk": combo_sk,
                            "post_id": post_id,
                            "title": title,
                            "slug": slug,
                        }
                    }
                })

        if transact_items:
            await dynamodb_transact_write_or_raise(table, transact_items)

        post.status = status

    return await with_dynamodb_table(fn)


async def get_popular_tags(limit: int = 10) -> List[Tag]:
    async def fn(table):
        resp = await table.query(
            IndexName="GSI_TAG_POPULARITY",
            KeyConditionExpression=Key("gsi_tag_pk").eq("TAG"),
            ScanIndexForward=False,
            Limit=limit
        )
        items = resp.get("Items", [])
        # logger.debug(items)
        # logger.debug(json.dumps(items, indent=4))
        return [
            Tag(
                name=item["tag_name"],
                posts_count=int(item["posts_count"]),
            ) for item in items
        ]

    return await with_dynamodb_table(fn)


async def search_tags_by_prefix(prefix: str, limit: int = 10) -> List[Tag]:
    async def fn(table):
        resp = await table.query(
            IndexName="GSI_TAG_NAME",
            KeyConditionExpression=Key("gsi_tag_name_pk").eq("TAG") & Key("tag_name").begins_with(prefix),
            Limit=limit,
            ScanIndexForward=True  # ascending alphabetical order
        )
        items = resp.get("Items", [])
        logger.debug(f"Tags: {items}")
        return [
            Tag(
                name=item["tag_name"],
                posts_count=int(item.get("posts_count", 0))
            )
            for item in items
        ]

    return await with_dynamodb_table(fn)


async def get_tags(query_dto: TagQueryDTO) -> List[Tag]:
    if query_dto.prefix:
        return await search_tags_by_prefix(
            prefix=query_dto.prefix,
            limit=query_dto.limit
        )
    return await get_popular_tags(
        limit=query_dto.limit
    )


async def create_contact_message(message_dto: ContactMessageDTO, user: User = None) -> ContactMessage:
    if user:
        verify_authorization(user, Permission.CREATE_CONTACT_MESSAGE)

    async def fn(table):
        now = utc_now_iso()
        message_id = str(uuid.uuid4())

        if is_prod():
            async with aioboto3_session().client("sns") as sns_client:
                text = (
                    f"New contact form submission:\n"
                    f"ID: {message_id}\n"
                    f"Name: {message_dto.name}\n"
                    f"Email: {message_dto.email}\n"
                    f"Message: {message_dto.message}\n"
                    f"User ID: {user.id if user else 'N/A'}"
                )
                await sns_client.publish(
                    TopicArn=get_contact_topic_arn(),
                    Message=text,
                    Subject="New Contact Form Submission"
                )

        message_item = {
            "pk": f"CONTACT_MESSAGE#{message_id}",
            "sk": "METADATA",
            "message_id": message_id,
            "name": message_dto.name,
            "email": message_dto.email,
            "message": message_dto.message,
            "created_at": now,
        }
        if user:
            message_item["user_id"] = user.id

        await table.put_item(Item=message_item)

        return ContactMessage(
            id=message_id,
            name=message_item["name"],
            email=str(message_item["email"]),
            message=message_item["message"],
            user_id=message_item.get("user_id"),
            created_at=now,
        )

    return await with_dynamodb_table(fn)


async def get_index_page_data(**kwargs: Any) -> Dict[str, Any]:
    return {
        **get_config(),
        **kwargs,
        "popular_tags": await get_popular_tags(10),
        "latest_posts": await get_latest_posts(10)
    }


async def get_posts_page_data(**kwargs: Any) -> Dict[str, Any]:
    data = {
        **get_config(),
        **kwargs,
        "post_items": await get_latest_posts(10)
    }

    request = data.get("request")
    data.update({
        "breadcrumbs": {
            data.get("index_breadcrumb", "Home"): get_url(request=request, name="index"),
            data.get("posts_breadcrumb", "Posts"): None,
        }
    })

    return data


async def get_post_page_data(post: Post, **kwargs: Any) -> Dict[str, Any]:
    data = {
        **get_config(),
        **kwargs,
        "post_item": post,
        "post_author": await find_user(post.user_id)
    }

    request = data.get("request")
    data.update({
        "breadcrumbs": {
            data.get("index_breadcrumb", "Home"): get_url(request=request, name="index"),
            data.get("posts_breadcrumb", "Posts"): get_url(request=request, name="posts-page"),
            post.title: None,
        }
    })

    return data


async def get_contacts_page_data(**kwargs: Any) -> Dict[str, Any]:
    data = {
        **get_config(),
        **kwargs
    }

    request = data.get("request")
    data.update({
        "breadcrumbs": {
            data.get("index_breadcrumb", "Home"): get_url(request=request, name="index"),
            data.get("contacts_breadcrumb", "Contacts"): None,
        }
    })

    return data


async def get_error_page_data(**kwargs: Any) -> Dict[str, Any]:
    return {
        **get_config(),
        **kwargs
    }


async def get_login_redirect_url(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/oauth2/authorize"
            f"?client_id={get_cognito_client_id()}"
            f"&response_type=code"
            f"&redirect_uri={quote(callback_url, safe='')}"
            f"&scope=openid+email+profile"
        )

    return callback_url


async def get_user_token_by_code(code: str, callback_url: str) -> UserToken:
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


async def get_logout_redirect_url(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/logout"
            f"?client_id={get_cognito_client_id()}"
            f"&logout_uri={quote(callback_url, safe='')}"
        )

    return callback_url
