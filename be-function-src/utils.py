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
from typing import Callable, Awaitable, ClassVar, Literal, TypeVar
from starlette.datastructures import State
from starlette.status import HTTP_200_OK
from jinja2 import Environment, FileSystemLoader, pass_context
import dotenv
import json
from datetime import datetime, timezone
from pydantic import BaseModel, EmailStr, Field, field_validator, conlist, constr, HttpUrl, computed_field
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from itertools import combinations
import time
from zoneinfo import ZoneInfo
import decimal
from urllib.parse import urlencode
import copy
import imghdr
from io import BytesIO
import struct
import random


class UserToken(BaseModel):
    sub: str
    iss: str  # "cognito", "google", etc.
    email: str | None = None
    name: str | None = None
    username: str | None = None  # only for Cognito native
    iat: datetime | None = None  # issued at
    exp: datetime | None = None  # expiration
    max_age: int | None = None
    aud: str | list[str] | None = None  # audience / client_id
    plain_token: str | None = None  # plain token


class UserStatus(str, Enum):
    ACTIVE = "active"
    BANNED = "banned"


class User(BaseModel):
    id: str
    owner_id: str | None = None
    email: str | None = None
    avatar_filename: str | None = None
    name: str
    username: str | None = None
    headline: str | None = None
    website: str | None = None
    address: str | None = None
    about: str | None = None
    providers: dict[str, dict[str, str | None]] = Field(default_factory=dict)  # noqa
    permissions: list[str] = Field(default_factory=lambda: [Permission.REGULAR])  # noqa
    status: UserStatus = UserStatus.ACTIVE
    published_posts_count: int
    unpublished_posts_count: int
    rejected_posts_count: int
    rating: int
    followers_count: int
    following_count: int
    created_at: int
    updated_at: int | None = None
    offset: str | None = None


class FileDTO(BaseModel):
    content: bytes
    filename: str

    MAX_IMAGE_SIZE: ClassVar[int] = 2 * 1024 * 1024  # 2 MB
    ALLOWED_IMAGE_TYPES: ClassVar[set[str]] = {"jpeg", "png", "gif"}

    @computed_field
    @property
    def size(self) -> int:
        size = len(self.content)
        if size > self.MAX_IMAGE_SIZE:
            raise ValueError(f"File too large: {size} bytes, max {self.MAX_IMAGE_SIZE}")
        return size

    @computed_field
    @property
    def type(self) -> str:
        image_type = imghdr.what(None, h=self.content)
        if image_type not in self.ALLOWED_IMAGE_TYPES:
            raise ValueError(f"Invalid image type: {image_type}")
        return image_type


class UserDTO(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    username: str | None = Field(None, min_length=3, max_length=30)

    _username_pattern: ClassVar[re.Pattern] = re.compile(r"^[a-z0-9-]+$")
    _username_blacklist: ClassVar[set[str]] = {"posts", "posts-fragment", "contacts", "post-tags", "users",
                                               "users-fragment",
                                               "dummy-fixtures"}

    @field_validator("username")
    @classmethod
    def validate_slug(cls, value: str):
        if value is None:
            return value

        value = value.strip()

        if not cls._username_pattern.match(value):
            raise ValueError("Username must be lowercase alphanumeric and may include single dashes only")

        if value.startswith("-") or value.endswith("-"):
            raise ValueError("Username cannot start or end with a dash")

        if "--" in value:
            raise ValueError("Username cannot contain consecutive dashes")

        if value in cls._username_blacklist:
            raise ValueError(f"'{value}' is a reserved word")

        return value


class UpdateUserDTO(UserDTO):
    avatar_action: Literal["delete", "replace", "keep"] | None = None
    # todo: check if file exists
    avatar_filename: str | None = None
    headline: str | None = Field(None, max_length=150)
    about: str | None = Field(None, max_length=2000)
    website: HttpUrl | None = None
    address: str | None = Field(None, max_length=255)


class UserImpressionAction(str, Enum):
    FOLLOW = "follow"
    BLOCK = "block"


class UserImpression(BaseModel):
    owner_id: str
    action: UserImpressionAction
    user_id: str
    target_user_id: str
    created_at: int
    updated_at: int | None = None


class UpdateUserImpressionDTO(BaseModel):
    action: UserImpressionAction = Field(...)


class ContactMessageDTO(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    message: str = Field(..., min_length=5, max_length=1000)


class ContactMessage(BaseModel):
    id: str
    name: str
    email: str
    message: str
    user_id: str | None = None
    created_at: int


class PostDTO(BaseModel):
    title: str = Field(..., min_length=20, max_length=500)
    content: str = Field(..., min_length=1000, max_length=10000)
    tags: conlist(constr(min_length=2, max_length=20), min_length=1, max_length=3)

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        if not value:
            return []
        # lowercase, kebab-case, dedupe
        normalized = [to_kebab_case(t) for t in value]
        return list(dict.fromkeys(normalized))


# todo: rename to ReplacePostDTO (?)
class UpdatePostDTO(PostDTO):
    pass


class BaseQueryDTO(BaseModel):
    DEFAULT_LIMIT: ClassVar[int] = 20

    offset: str | None = None
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=20)

    def get_dict(self, rewrite: dict[str, any] | None = None) -> dict[str, any]:
        """Return a dictionary representation of the model."""
        data = self.model_dump()
        if rewrite:
            data.update(rewrite)
        return enum_to_value(data)

    def has_params(self) -> bool:
        """Return True if any field differs from its default value."""
        for name, info in self.model_fields.items():
            value = getattr(self, name)

            # skip fields that are None or empty lists/dicts
            if value in (None, [], {}):
                continue

            # compare with default if it exists
            if info.default is not None:
                if value != info.default:
                    return True
            else:
                # field has no default, any non-empty value counts
                return True
        return False


class PostQueryType(str, Enum):
    POPULAR = "popular"


class PostQueryDTO(BaseQueryDTO):
    tags: list[str] | None = Field(default_factory=list)  # noqa
    type: PostQueryType | None = None


class UserQueryType(str, Enum):
    POPULAR = "popular"


class UserQueryDTO(BaseQueryDTO):
    type: UserQueryType | None = None


class TagQueryDTO(BaseQueryDTO):
    prefix: str | None = Field(None, min_length=1, max_length=10)


class Tag(BaseModel):
    name: str
    rating: int
    offset: str | None = None


class PublicTag(BaseModel):
    name: str


class PostStatus(str, Enum):
    UNPUBLISHED = "unpublished"
    PUBLISHED = "published"
    REJECTED = "rejected"


class UpdatePostStatusDTO(BaseModel):
    status: PostStatus = Field(...)
    comment: str = Field(None)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value, info):
        status = info.data.get("status")
        if status == PostStatus.REJECTED and not value:
            raise ValueError("Comment is required when rejecting a post")
        if status != PostStatus.REJECTED and value:
            raise ValueError("Comment is only allowed when rejecting a post")
        return value


class PostImpressionAction(str, Enum):
    LIKE = "like"
    DISLIKE = "dislike"


class PostImpression(BaseModel):
    owner_id: str
    post_id: str
    action: PostImpressionAction
    user_id: str
    created_at: int
    updated_at: int | None = None


class UpdatePostImpressionDTO(BaseModel):
    action: PostImpressionAction = Field(...)


class Post(BaseModel):
    id: str
    owner_id: str
    title: str
    slug: str
    user_id: str
    user_slug: str | None
    content: str
    tags: list[str]
    status: PostStatus = PostStatus.UNPUBLISHED
    comment: str | None = None
    rating: int
    likes_count: int
    dislikes_count: int
    created_at: int
    updated_at: int | None = None
    offset: str | None = None


class Permission(str, Enum):
    REGULAR = "regular"
    ROOT = "root"
    ALL = "*"

    UPDATE_USER = "update_user"
    UPDATE_USER_IMPRESSION = "update_user_impression"

    CREATE_POST = "create_post"
    UPDATE_POST = "update_post"
    UPDATE_POST_STATUS = "update_post_status"
    CREATE_CONTACT_MESSAGE = "create_contact_message"
    UPDATE_POST_IMPRESSION = "toggle_post_impression"


class BaseError(Exception):
    def __init__(self, message: str = "An error occurred", field: str = None):
        self.message = message
        self.field = field
        super().__init__(self.message)

    def to_dict(self):
        if self.field:
            return {self.field: self.message}
        return {"error": self.message}


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
    def __init__(self, message: str = "Slug already exists", field: str = "title"):
        super().__init__(message=message, field=field)


class PostNotFoundError(BaseError):
    pass


class PostAlreadyPublishedError(BaseError):
    def __init__(self, message: str = "Post already published", field: str = "title"):
        super().__init__(message=message, field=field)


class UserNotFoundError(BaseError):
    pass


class AuthorizationFailedError(BaseError):
    def __init__(self, permission: str, message: str = None):
        self.permission = permission
        super().__init__(message=message if message else f"User lacks required permission: {permission}")


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
        "public_s3_bucket": os.getenv("PUBLIC_S3_BUCKET"),
        "permission_hierarchy": {
            Permission.REGULAR: [
                Permission.UPDATE_USER_IMPRESSION,
                Permission.CREATE_POST,
                Permission.UPDATE_POST_IMPRESSION,
                Permission.CREATE_CONTACT_MESSAGE,
            ],
            Permission.ROOT: [
                Permission.ALL
            ],
        },
        "default_avatar_colors": {
            "A": "#F44336",  # Red
            "B": "#E91E63",  # Pink
            "C": "#9C27B0",  # Purple
            "D": "#673AB7",  # Deep Purple
            "E": "#3F51B5",  # Indigo
            "F": "#2196F3",  # Blue
            "G": "#03A9F4",  # Light Blue
            "H": "#00BCD4",  # Cyan
            "I": "#009688",  # Teal
            "J": "#4CAF50",  # Green
            "K": "#8BC34A",  # Light Green
            "L": "#CDDC39",  # Lime
            "M": "#FFEB3B",  # Yellow
            "N": "#FFC107",  # Amber
            "O": "#FF9800",  # Orange
            "P": "#FF5722",  # Deep Orange
            "Q": "#795548",  # Brown
            "R": "#9E9E9E",  # Grey
            "S": "#607D8B",  # Blue Grey
            "T": "#FF1744",  # Bright Red
            "U": "#D500F9",  # Bright Purple
            "V": "#00E676",  # Bright Green
            "W": "#00B0FF",  # Bright Cyan
            "X": "#FFD600",  # Bright Yellow
            "Y": "#FF6D00",  # Bright Orange
            "Z": "#C51162"  # Bright Pink
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


def get_public_s3_bucket() -> str:
    return config.get("public_s3_bucket")


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


def get_permission_hierarchy() -> dict[str, list[str]]:
    return get_config().get("permission_hierarchy")


class Lazy:
    def __init__(self, factory: callable):
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
        permissions: list[str] | None = None,
        hierarchy: dict[str, list[str]] | None = None,
) -> bool:
    """
    Verify if user has access to perform action requiring `permission`.
    """
    hierarchy = hierarchy or get_permission_hierarchy()

    # Owner check
    if resource:
        data = resource.model_dump()
        owner_id = data.get("owner_id")
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


def check_authorization(
        user: User,
        permission: str,
        resource: BaseModel = None,
        permissions: list[str] | None = None,
        hierarchy: dict[str, list[str]] | None = None
) -> bool:
    try:
        verify_authorization(user, permission, resource, permissions, hierarchy)
        return True
    except AuthorizationFailedError:
        return False


def to_kebab_case(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


def utc_now() -> int:
    return int(time.time() * 1000)


async def dynamodb_transact_write(transacts: list[dict[str, any]]):
    """
    Executes a DynamoDB TransactWriteItems call and raises a
    DynamoTransactionError with detailed reasons if it fails.
    """
    try:
        table = await get_dynamodb_table()
        await table.meta.client.transact_write_items(TransactItems=transacts)
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
def jinja2_url_for(ctx, name: str, **params) -> str:
    request = ctx.get("request")
    if not request:
        raise ValueError("Request not found in context")

    # find the route
    route = next(r for r in request.app.routes if getattr(r, "name", None) == name)
    path_param_names = getattr(route, "param_convertors", {}).keys()

    # split params into path vs query, skipping None
    path_params = {k: v for k, v in params.items() if k in path_param_names and v is not None}
    query_params = {k: v for k, v in params.items() if k not in path_param_names and v is not None}

    url = str(request.url_for(name, **path_params))

    if query_params:
        items = []
        for k, v in query_params.items():
            if isinstance(v, bool):
                v = int(v)  # True -> 1, False -> 0
            if isinstance(v, (list, tuple)):
                items.extend((k, int(i) if isinstance(i, bool) else i) for i in v)
            else:
                items.append((k, v))
        if items:
            url = f"{url}?{urlencode(items)}"

    return url


@pass_context
def jinja2_user_url(ctx, user: User, **params) -> str:
    if user.username:
        return jinja2_url_for(ctx, "user-by-username", username=user.username, **params)
    return jinja2_url_for(ctx, "user", user_id=user.id, **params)


def get_user_url(request, user: User, **params) -> str:
    if user.username:
        return get_url(request, "user-by-username", username=user.username, **params)
    return get_url(request, "user", user_id=user.id, **params)


@pass_context
def jinja2_post_url(ctx, post: Post, **params) -> str:
    return get_post_url(ctx.get("request"), post, **params)


def get_post_url(request, post: Post, **params) -> str:
    if post.user_slug:
        return get_url(request, "post-by-slugs", user_slug=post.user_slug, post_slug=post.slug, **params)
    return get_url(request, "post", post_id=post.id, **params)


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
    jinja2_env.filters.update({
        "unix_to_month_year": unix_to_month_year,
        "unix_to_full_date": unix_to_full_date
    })
    jinja2_env.globals.update(get_config())
    jinja2_env.globals.update({
        "static_url": static_url,
        "url": jinja2_url_for,
        "user_url": jinja2_user_url,
        "full_url": full_url_for,
        "post_url": jinja2_post_url,
        "Permission": Permission,
        "check_auth": check_authorization,
        "PostStatus": PostStatus,
        "PostImpressionAction": PostImpressionAction,
        "UserImpressionAction": UserImpressionAction,
        "PostQueryType": PostQueryType,
        "UserQueryType": UserQueryType,
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
    logger.debug("Boto3 session loading")
    return aioboto3.Session(**args)


def get_dynamodb_resource_kwargs():
    return {} if is_prod() else {
        "aws_access_key_id": "dummy",
        "aws_secret_access_key": "dummy",
        "region_name": get_aws_region(),
        "endpoint_url": get_dynamodb_endpoint(),
    }


def get_s3_client_kwargs():
    return {
        "region_name": get_aws_region()
    }


aioboto3_session = Lazy(get_aioboto3_session)

# Global DynamoDB table
dynamodb_table = None


async def get_dynamodb_table():
    global dynamodb_table
    if dynamodb_table is None:
        session = aioboto3_session()
        async with session.resource("dynamodb", **get_dynamodb_resource_kwargs()) as dynamodb:
            dynamodb_table = await dynamodb.Table(get_dynamodb_table_name())
            logger.debug("DynamoDB table resource loading")
    return dynamodb_table


async def with_s3_client(fn: Callable[[any], Awaitable[any]]):
    session = aioboto3_session()
    async with session.client("s3", **get_s3_client_kwargs()) as s3_client:
        return await fn(s3_client)


def get_html_content(template: str, data: dict[str, any]) -> str:
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


def get_image_dimensions(data: bytes) -> tuple[int, int]:
    """Return (width, height) for JPEG, PNG, GIF images from raw bytes."""

    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24:
            raise ValueError("PNG file too short")
        width, height = struct.unpack(">II", data[16:24])
        return width, height

    # GIF
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) < 10:
            raise ValueError("GIF file too short")
        width, height = struct.unpack("<HH", data[6:10])
        return width, height

    # JPEG
    elif data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 1 < len(data):
            if data[offset] != 0xFF:
                raise ValueError("Invalid JPEG marker")
            marker = data[offset + 1]

            if 0xC0 <= marker <= 0xC3:
                # need at least 5 bytes for >xHH
                segment = data[offset + 5:offset + 10]
                if len(segment) < 5:
                    raise ValueError("JPEG SOF segment too short")
                _, height, width = struct.unpack(">xHH", segment)
                return width, height
            else:
                if offset + 4 > len(data):
                    raise ValueError("Truncated JPEG")
                seg_len = struct.unpack(">H", data[offset + 2:offset + 4])[0]
                if seg_len < 2:
                    raise ValueError("Invalid segment length")
                offset += 2 + seg_len

        raise ValueError("No SOF marker found in JPEG")

    raise ValueError("Unsupported image type")


async def save_public_file(file_dto: FileDTO) -> str:
    file_ext = file_dto.type
    filename = str(uuid.uuid4())
    if file_ext in FileDTO.ALLOWED_IMAGE_TYPES:
        try:
            width, height = get_image_dimensions(file_dto.content)
            filename += f"_{width}x{height}"
        except ValueError:
            pass
    filename += f".{file_ext}"

    if not is_prod():
        with open(f"./static/{filename}", "wb") as f:
            f.write(file_dto.content)
        return filename

    async def fn(s3_client):
        stream = BytesIO(file_dto.content)
        stream.seek(0)

        await s3_client.upload_fileobj(stream, get_public_s3_bucket(), filename)
        return filename

    return await with_s3_client(fn)


async def drop_public_file(filename: str) -> None:
    if not is_prod():
        path = os.path.join("./static", filename)
        if os.path.exists(path):
            os.remove(path)
        return

    async def fn(s3_client):
        await s3_client.delete_object(Bucket=get_public_s3_bucket(), Key=filename)

    await with_s3_client(fn)


async def configure_app_state(app_state: State) -> None:
    if is_prod():
        # Startup: fetch JWKS
        # Fetch JWKS keys for token validation
        app_state.jwks = await get_cognito_jwks()


def to_datetime(ts: any) -> datetime | None:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


async def get_user_by_user_token(token: UserToken) -> User | None:
    table = await get_dynamodb_table()
    provider_user_item = None
    user_item = None
    user_id = None

    # 1: Lookup provider user record
    if token.sub:
        resp = await table.get_item(
            Key={
                "pk": f"PROVIDER_USER#{token.iss}#{token.sub}",
                "sk": "META"
            }
        )
        provider_user_item = resp.get("Item")
        if provider_user_item:
            user_id = provider_user_item["user_id"]

            # Fetch user record
            resp = await table.get_item(
                Key={
                    "pk": f"USER#{user_id}",
                    "sk": "META"
                }
            )
            user_item = resp.get("Item")

    # 2: Fallback: lookup user by email
    # todo: user_item instead of provider_user_item (?)
    if not provider_user_item and token.email:
        resp = await table.query(
            IndexName="USERS_BY_EMAIL",
            KeyConditionExpression=Key("user_email_pk").eq(token.email),
            Limit=1
        )
        items = resp.get("Items", [])
        if items:
            user_item = items[0]
            user_id = user_item["id"]

    # 3: Not found
    if not user_item:
        return None

    return user_from_dynamodb({
        "id": user_id,
        **user_item,
        "user_email_pk": user_item.get("user_email_pk") or token.email,
        "name": user_item.get("name") or token.name,
        "username": user_item.get("username") or token.username
    })


def build_user_name(raw_name: str | None, now: int) -> str:
    if not raw_name:
        return f"User {now}"

    return raw_name


def build_user_username(raw_name: str | None, raw_username: str | None, now: int) -> str | None:
    base = raw_username or raw_name
    if not base:
        return None

    # Lowercase
    username = base.lower()

    # Replace invalid characters with dash
    username = re.sub(r"[^a-z0-9]+", "-", username)

    # Remove consecutive dashes
    username = re.sub(r"-{2,}", "-", username)

    # Remove leading/trailing dashes
    username = username.strip("-").strip()

    if not username:
        return None

    # Append timestamp for uniqueness
    username += f"-{now}"

    return username


async def upsert_user_by_user_token(token: UserToken, status: UserStatus = UserStatus.ACTIVE) -> User:
    await get_dynamodb_table()
    now = utc_now()

    user = await get_user_by_user_token(token)
    if user:
        user_id = user.id
        providers = user.providers
    else:
        user_id = str(uuid.uuid4())
        providers = {}

    providers[token.iss] = {"sub": token.sub, "username": token.username, "name": token.name}

    transacts = []

    if user:
        add_dynamodb_update_transact(transacts, (f"USER#{user_id}", "META"), {"providers": providers})
        user.providers = providers
    else:
        user_item = {
            "id": user_id,
            "user_email_pk": token.email,
            "name": build_user_name(token.name, now),
            "providers": providers,
            "status": status,
            "rating_sk": compute_rating_sk(0, now),
            "created_at_sk": now,
            "user_status_pk": f"USER#STATUS#{status.value}",
        }
        username = build_user_username(token.name, token.username, now)
        if username:
            user_item["username"] = username
            add_dynamodb_put_transact(transacts, (f"USER_SLUG#{username}", "META"), {"user_id": user_id},
                                      new_pk_only=True)
        add_dynamodb_put_transact(transacts, (f"USER#{user_id}", "META"), user_item)
        user = user_from_dynamodb(user_item)

    add_dynamodb_put_transact(transacts, (f"PROVIDER_USER#{token.iss}#{token.sub}", "META"), {
        "user_id": user_id,
        "email": token.email,
        "created_at": now,
        "updated_at": now
    })

    try:
        await dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="username")
        raise

    return user


def user_token_from_jwt_claims(claims: dict[str, any], plain_token: str = None) -> UserToken:
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


def get_dummy_user_token(
        *,
        sub: str = "test-sub",
        iss: str = "test-iss",
        username: str | None = None,
        email: str = "test@example.com",
        name: str | None = None
) -> UserToken:
    return UserToken(
        sub=sub,
        iss=iss,
        username=username,
        email=email,
        name=name,
        iat=None,
        exp=None,
        max_age=None,
        aud="",
        plain_token="dummy"
    )


async def get_user_token_by_plain_token(plain_token: str | None, app_state: State) -> UserToken | None:
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
        return user_token_from_jwt_claims(claims, plain_token)
    except JWTError:
        raise InvalidTokenError("Invalid token")


async def get_user_by_plain_token(plain_token: str | None, app_state: State) -> User | None:
    user_token = await get_user_token_by_plain_token(plain_token, app_state)

    if user_token is None:
        return None

    # logger.debug(f"user_token: {user_token}")

    user = await get_user_by_user_token(user_token)
    # logger.debug(f"user: {user}")

    return user


def post_from_dynamodb(d_item: dict[str, any]) -> Post:
    owner_id = d_item["user_id"]
    return Post(
        id=d_item["id"],
        owner_id=owner_id,
        title=d_item["title"],
        slug=d_item["post_slug"],
        user_id=owner_id,
        content=d_item["content"],
        tags=d_item.get("tags", []),
        status=d_item["status"],
        comment=d_item.get("comment"),
        rating=d_item["rating_sk"],
        likes_count=d_item.get("likes_count", 0),
        dislikes_count=d_item.get("dislikes_count", 0),
        user_slug=d_item.get("user_slug"),
        created_at=d_item["created_at_sk"],
        updated_at=d_item.get("updated_at"),
    )


def compute_rating_sk(rating: int, created_at: int = 0) -> int:
    return rating * 10_000_000_000_000 + created_at


async def create_post(post_dto: PostDTO, user: User) -> Post:
    verify_authorization(user, Permission.CREATE_POST)

    await get_dynamodb_table()
    now = utc_now()
    status = PostStatus.UNPUBLISHED
    post_id = str(uuid.uuid4())
    slug = to_kebab_case(post_dto.title)

    transacts = []

    post_item = {
        "id": post_id,
        "title": post_dto.title,
        "post_slug": slug,
        "user_id": user.id,
        "content": post_dto.content,
        "tags": post_dto.tags,
        "rating_sk": compute_rating_sk(0, now),
        "status": status,
        "created_at_sk": now,
        "post_status_pk": f"POST#STATUS#{status.value}",
        "post_user_status_pk": f"POST#USER#{user.id}#STATUS#{status.value}",
    }
    if user.username:
        post_item["user_slug"] = user.username
    add_dynamodb_put_transact(transacts, (f"POST#{post_id}", "META"), post_item, new_pk_only=True)
    add_dynamodb_update_transact(transacts, (f"USER#{user.id}", "META"), deltas={"unpublished_posts_count": 1})
    add_dynamodb_put_transact(transacts, (f"POST_SLUG#{slug}", "META"), {"post_id": post_id}, new_pk_only=True)

    try:
        await dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    return post_from_dynamodb(post_item)


async def update_post(post: Post, update_post_dto: UpdatePostDTO, cur_user: User) -> None:
    verify_authorization(cur_user, Permission.UPDATE_POST, post)

    if post.status == PostStatus.PUBLISHED:
        raise PostAlreadyPublishedError()

    changes = update_post_dto.model_dump(exclude_unset=True)
    if not changes:
        return

    await get_dynamodb_table()
    transacts = []

    if "title" in changes:
        # todo: normalize like username
        new_slug = to_kebab_case(changes["title"])
        old_slug = post.slug
        if old_slug == new_slug:
            pass
        add_dynamodb_delete_transact(transacts, (f"POST_SLUG#{old_slug}", "META"))
        add_dynamodb_put_transact(transacts, (f"POST_SLUG#{new_slug}", "META"), {"post_id": post.id}, new_pk_only=True)
        changes["post_slug"] = new_slug

    add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), changes)

    try:
        await dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    for key, value in changes.items():
        setattr(post, key, value)


async def find_post(post_id: str) -> Post | None:
    table = await get_dynamodb_table()
    resp = await table.get_item(
        Key={
            "pk": f"POST#{post_id}",
            "sk": "META"
        }
    )
    item = resp.get("Item")
    if not item:
        return None

    return post_from_dynamodb(item)


async def get_post(post_id: str) -> Post:
    post = await find_post(post_id)
    if post is None:
        raise PostNotFoundError(f"Post '{post_id}' not found")
    return post


async def find_post_by_slug(slug: str) -> Post | None:
    table = await get_dynamodb_table()
    resp = await table.query(
        IndexName="POSTS_BY_SLUG",
        KeyConditionExpression=Key("post_slug").eq(slug),
        Limit=1
    )
    items = resp.get("Items", [])
    if not items:
        return None
    item = items[0]
    # logger.debug(f"Post by slug: {item}")
    return post_from_dynamodb(item)


async def get_post_by_slugs(user_slug: str, post_slug: str) -> Post:
    post = await find_post_by_slug(post_slug)
    if post is None:
        raise PostNotFoundError(f"Post '{post_slug}' not found")
    if post.user_slug != user_slug:
        raise UserNotFoundError(f"User '{user_slug}' not found")
    return post


def user_from_dynamodb(d_item: dict[str, any]) -> User:
    owner_id = d_item["id"]
    return User(
        id=owner_id,
        owner_id=owner_id,
        email=d_item.get("user_email_pk"),
        avatar_filename=d_item.get("avatar_filename"),
        name=d_item["name"],
        username=d_item.get("username"),
        headline=d_item.get("headline"),
        website=d_item.get("website"),
        address=d_item.get("address"),
        about=d_item.get("about"),
        providers=d_item.get("providers", {}),
        permissions=d_item.get("permissions", [Permission.REGULAR]),
        status=d_item.get("status", UserStatus.ACTIVE),
        published_posts_count=d_item.get("published_posts_count", 0),
        unpublished_posts_count=d_item.get("unpublished_posts_count", 0),
        rejected_posts_count=d_item.get("rejected_posts_count", 0),
        rating=d_item["rating_sk"],
        followers_count=d_item.get("followers_count", 0),
        following_count=d_item.get("following_count", 0),
        created_at=d_item["created_at_sk"],
        updated_at=d_item.get("updated_at")
    )


async def find_user(user_id: str) -> User | None:
    table = await get_dynamodb_table()
    resp = await table.get_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": "META"
        }
    )
    item = resp.get("Item")
    if not item:
        return None
    # logger.debug(f"User: {item}")
    return user_from_dynamodb(item)


def user_impression_from_dynamodb(d_item: dict[str, any]) -> UserImpression:
    user_id = d_item["user_id"]
    return UserImpression(
        owner_id=user_id,
        user_id=user_id,
        target_user_id=d_item["target_user_id"],
        action=d_item["action"],
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at")
    )


async def find_user_impression(user: User, cur_user: User) -> UserImpression | None:
    table = await get_dynamodb_table()
    resp = await table.get_item(
        Key={
            "pk": f"USER#{cur_user.id}",
            "sk": f"REL#USER#{user.id}"
        }
    )
    item = resp.get("Item")
    if not item:
        return None
    # logger.debug(f"UserImpression: {item}")
    return user_impression_from_dynamodb(item)


def build_dynamodb_put_item_params(
        key: tuple[str, str],
        values: dict[str, any],
        add_created_at: bool = True,
        new_pk_only: bool = False
) -> dict[str, any]:
    # Set created_at
    if add_created_at:
        values["created_at"] = utc_now()

    pk, sk = key
    params = {
        "TableName": dynamodb_table.name,
        "Item": {
            **values,
            "pk": pk,
            "sk": sk
        }
    }
    if new_pk_only:
        params["ConditionExpression"] = "attribute_not_exists(pk)"
    return {
        "Put": params
    }


def add_dynamodb_put_transact(
        transacts: list,
        key: tuple[str, str],
        values: dict[str, any],
        add_created_at: bool = True,
        new_pk_only: bool = False
) -> None:
    param_dict = dict(locals())
    param_dict.pop("transacts", None)
    transacts.append(build_dynamodb_put_item_params(**param_dict))


def build_dynamodb_update_item_params(
        key: tuple[str, str],
        changes: dict[str, any] | None = None,
        deltas: dict[str, any] | None = None,
        add_updated_at: bool = True
) -> dict[str, any]:
    set_parts = []
    remove_parts = []
    add_parts = []
    expr_attr_names = {}
    expr_attr_values = {}

    # Set updated_at
    if add_updated_at:
        if not changes:
            changes = {}
        changes["updated_at"] = utc_now()

    # Handle normal changes (SET / REMOVE)
    if changes:
        for field, value in changes.items():
            name_alias = f"#new_{field}"
            value_alias = f":new_{field}"
            expr_attr_names[name_alias] = field

            if value is None:
                remove_parts.append(name_alias)
            else:
                set_parts.append(f"{name_alias} = {value_alias}")
                expr_attr_values[value_alias] = value

    # Handle numeric deltas (ADD)
    if deltas:
        for field, delta in deltas.items():
            name_alias = f"#delta_{field}"
            value_alias = f":delta_{field}"
            expr_attr_names[name_alias] = field
            expr_attr_values[value_alias] = delta
            add_parts.append(f"{name_alias} {value_alias}")

    # Combine expressions
    update_expr_parts = []
    if set_parts:
        update_expr_parts.append("SET " + ", ".join(set_parts))
    if add_parts:
        update_expr_parts.append("ADD " + ", ".join(add_parts))
    if remove_parts:
        update_expr_parts.append("REMOVE " + ", ".join(remove_parts))

    update_expr = " ".join(update_expr_parts)

    pk, sk = key
    # logger.debug(f"DynamoDB UpdateExpression: {update_expr}")

    return {
        "Update": {
            "TableName": dynamodb_table.name,
            "Key": {"pk": pk, "sk": sk},
            "UpdateExpression": update_expr,
            "ExpressionAttributeNames": expr_attr_names,
            "ExpressionAttributeValues": expr_attr_values,
        }
    }


def add_dynamodb_update_transact(
        transacts: list,
        key: tuple[str, str],
        changes: dict[str, any] | None = None,
        deltas: dict[str, any] | None = None,
        add_updated_at: bool = True
) -> None:
    param_dict = dict(locals())
    param_dict.pop("transacts", None)
    transacts.append(build_dynamodb_update_item_params(**param_dict))


def build_dynamodb_delete_item_params(key: tuple[str, str]) -> dict[str, any]:
    pk, sk = key

    return {
        "Delete": {
            "TableName": dynamodb_table.name,
            "Key": {
                "pk": pk,
                "sk": sk
            }
        }
    }


def add_dynamodb_delete_transact(
        transacts: list,
        key: tuple[str, str]
) -> None:
    param_dict = dict(locals())
    param_dict.pop("transacts", None)
    transacts.append(build_dynamodb_delete_item_params(**param_dict))


async def update_dynamodb_item(
        key: tuple[str, str],
        changes: dict[str, any] | None = None,
        deltas: dict[str, any] | None = None,
        add_updated_at: bool = True
) -> None:
    param_dict = dict(locals())
    table = await get_dynamodb_table()
    update_item_params = build_dynamodb_update_item_params(**param_dict)
    await table.update_item(**update_item_params["Update"])


async def update_user(user: User, update_user_dto: UpdateUserDTO, cur_user: User) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)

    changes = update_user_dto.model_dump(exclude_unset=True)
    if not changes:
        return

    await get_dynamodb_table()
    transacts = []

    if changes.get("website"):
        changes["website"] = str(changes["website"])

    avatar_action = changes.pop("avatar_action", "keep")

    if avatar_action == "delete":
        changes["avatar_filename"] = None
    elif avatar_action == "replace":
        pass
    elif avatar_action == "keep":
        changes.pop("avatar_filename", None)

    old_avatar = user.avatar_filename

    if "username" in changes:
        new_slug = changes["username"]
        old_slug = user.username
        if old_slug == new_slug:
            pass
        add_dynamodb_delete_transact(transacts, (f"USER_SLUG#{old_slug}", "META"))
        add_dynamodb_put_transact(transacts, (f"USER_SLUG#{new_slug}", "META"), {"user_id": user.id}, new_pk_only=True)
        posts = await get_latest_published_posts_by_user(user)
        for post in posts:
            add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), {"user_slug": new_slug})
    else:
        old_slug = user.username
        add_dynamodb_delete_transact(transacts, (f"USER_SLUG#{old_slug}", "META"))
        posts = await get_latest_published_posts_by_user(user)
        for post in posts:
            add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), {"user_slug": None})

    add_dynamodb_update_transact(transacts, (f"USER#{user.id}", "META"), changes)

    try:
        await dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="username")
        raise

    if old_avatar and avatar_action in {"delete", "replace"}:
        await drop_public_file(old_avatar)

    for key, value in changes.items():
        setattr(user, key, value)


async def get_user(user_id: str) -> User:
    user = await find_user(user_id)
    if user is None:
        raise UserNotFoundError(f"User '{user_id}' not found")
    return user


async def find_user_by_username(username: str) -> User | None:
    table = await get_dynamodb_table()
    resp = await table.query(
        IndexName="USERS_BY_USERNAME",
        KeyConditionExpression=Key("username").eq(username),
        Limit=1
    )
    items = resp.get("Items", [])
    if not items:
        return None
    item = items[0]
    # logger.debug(f"User: {item}")
    return user_from_dynamodb(item)


async def get_user_by_username(username: str) -> User:
    user = await find_user_by_username(username)
    if user is None:
        raise UserNotFoundError(f"User '{username}' not found")
    return user


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            # you can cast to int if you know it’s always an integer
            if o % 1 == 0:
                return int(o)
            return float(o)
        return super().default(o)


def encode_offset(offset: dict) -> str | None:
    if not offset:
        return None
    return base64.urlsafe_b64encode(
        json.dumps(offset, cls=DecimalEncoder).encode()
    ).decode()


def decode_offset(token: str) -> dict | None:
    if not token:
        return None
    return json.loads(
        base64.urlsafe_b64decode(token.encode()).decode()
    )


async def get_published_posts(query_dto: PostQueryDTO = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()
    if query_dto.type == PostQueryType.POPULAR:
        if query_dto.tags:
            return await get_popular_published_posts_by_tags(query_dto)
        return await get_popular_published_posts(query_dto)
    if query_dto.tags:
        return await get_latest_published_posts_by_tags(query_dto)
    return await get_latest_published_posts(query_dto)


T = TypeVar("T")


async def query_dynamodb_items(
        query_dto: BaseQueryDTO,
        index_name: str,
        key_condition_expr: any,
        map_fn: Callable[[dict], T],
) -> list[T]:
    """Generic DynamoDB query executor with pagination and mapping."""

    table = await get_dynamodb_table()
    query_args = {
        "IndexName": index_name,
        "KeyConditionExpression": key_condition_expr,
        "ScanIndexForward": False,
        "Limit": query_dto.limit,
    }

    if query_dto.offset:
        query_args["ExclusiveStartKey"] = decode_offset(query_dto.offset)

    resp = await table.query(**query_args)
    items = resp.get("Items", [])
    results = [map_fn(item) for item in items]

    # Handle pagination
    if len(results) == query_dto.limit:
        results[-1].offset = encode_offset(resp.get("LastEvaluatedKey"))

    return results


async def get_latest_published_posts(query_dto: PostQueryDTO = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()

    return await query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_STATUS_CREATED_AT",
        key_condition_expr=Key("post_status_pk").eq(f"POST#STATUS#{PostStatus.PUBLISHED.value}"),
        map_fn=post_from_dynamodb,
    )


async def get_popular_published_posts(query_dto: PostQueryDTO = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()

    return await query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_STATUS_RATING",
        key_condition_expr=Key("post_status_pk").eq(f"POST#STATUS#{PostStatus.PUBLISHED.value}"),
        map_fn=post_from_dynamodb,
    )


async def get_latest_published_posts_by_tags(query_dto: PostQueryDTO = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()
    if not query_dto.tags:
        return await get_latest_published_posts(query_dto)

    table = await get_dynamodb_table()
    query_args = {
        "KeyConditionExpression": Key("pk").eq("POST_TAG_COMBO#" + "#".join(sorted(query_dto.tags))),
        "ScanIndexForward": False,
        "Limit": query_dto.limit,
    }
    if query_dto.offset:
        query_args["ExclusiveStartKey"] = decode_offset(query_dto.offset)
    resp = await table.query(**query_args)
    combo_items = resp.get("Items", [])
    # logger.debug(combo_items)
    if not combo_items:
        return []

    # Batch get post metadata
    post_ids = [item["post_id"] for item in combo_items]
    keys = [{"pk": f"POST#{post_id}", "sk": "META"} for post_id in post_ids]
    resp = await table.meta.client.batch_get_item(RequestItems={table.name: {"Keys": keys}})
    post_items = resp["Responses"].get(table.name, [])

    # Maintain original order
    post_items_map = {item["id"]: item for item in post_items}
    ordered_posts = [post_items_map[pid] for pid in post_ids if pid in post_items_map]

    posts = [post_from_dynamodb(item) for item in ordered_posts]
    if len(posts) == query_dto.limit:
        posts[-1].offset = encode_offset(resp.get("LastEvaluatedKey"))
    return posts


async def get_popular_published_posts_by_tags(query_dto: PostQueryDTO = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()

    # Increase limit to fetch more posts before filtering
    query_dto_copy = copy.copy(query_dto)
    query_dto_copy.limit = max(query_dto.limit * 5, 100)

    posts = await get_popular_published_posts(query_dto_copy)

    if not query_dto.tags:
        return posts

    offset = posts[-1].offset if posts else None

    # Filter by tags
    filtered_posts = [post for post in posts if set(query_dto.tags).issubset(set(post.tags))]
    if filtered_posts:
        filtered_posts[-1].offset = offset

    return filtered_posts


async def update_post_status(post: Post, update_post_status_dto: UpdatePostStatusDTO, user: User) -> None:
    # logger.debug(f"update_post_status: user: {user}")
    verify_authorization(user, Permission.UPDATE_POST_STATUS)

    if post.status == PostStatus.PUBLISHED:
        raise PostAlreadyPublishedError()

    changes = update_post_status_dto.model_dump(exclude_unset=True)
    if not changes:
        return

    old_status = post.status
    status = changes["status"]

    table = await get_dynamodb_table()
    now = utc_now()

    transacts = []

    # Update post
    add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), {
        **changes,
        "post_status_pk": f"POST#STATUS#{status.value}",
        "post_user_status_pk": f"POST#USER#{post.user_id}#STATUS#{status.value}",
    })

    # User post counters
    owner = await find_user(post.user_id)
    if owner:
        deltas = {
            f"{old_status.value}_posts_count": -1,
            f"{status.value}_posts_count": 1,
        }
        add_dynamodb_update_transact(transacts, (f"USER#{owner.id}", "META"), deltas=deltas)
        for key, delta in deltas.items():
            setattr(owner, key, getattr(owner, key) + delta)

    if status == PostStatus.PUBLISHED:
        # Upsert tags
        for tag in post.tags:
            transacts.append({
                "Update": {
                    "TableName": table.name,
                    "Key": {
                        "pk": f"POST_TAG#{tag}",
                        "sk": "META"
                    },
                    "UpdateExpression": (
                        "SET #new_tag_name_sk = if_not_exists(#new_tag_name_sk, :tag_name_sk), "
                        "    #new_tag_type_pk = if_not_exists(#new_tag_type_pk, :tag_type_pk), "
                        "    #new_rating_sk = if_not_exists(#new_rating_sk, :def_rating_sk) + :rating_sk_inc, "
                        "    #new_created_at = if_not_exists(#new_created_at, :now), "
                        "    #new_updated_at = :now "
                    ),
                    "ExpressionAttributeNames": {
                        "#new_tag_name_sk": "tag_name_sk",
                        "#new_tag_type_pk": "tag_type_pk",
                        "#new_rating_sk": "rating_sk",
                        "#new_created_at": "created_at",
                        "#new_updated_at": "updated_at",
                    },
                    "ExpressionAttributeValues": {
                        ":tag_name_sk": tag,
                        ":tag_type_pk": "POST_TAG",
                        ":now": now,
                        ":def_rating_sk": compute_rating_sk(0, now),
                        ":rating_sk_inc": compute_rating_sk(1)
                    }
                }
            })

        # Create post tag combos
        for r in range(1, len(post.tags) + 1):
            for combo in combinations(sorted(post.tags), r):
                add_dynamodb_put_transact(transacts, ("POST_TAG_COMBO#" + "#".join(combo), str(now)), {
                    "post_id": post.id
                })

    # logger.debug(transacts)

    await dynamodb_transact_write(transacts)
    post.status = status


def tag_from_dynamodb(d_item: dict[str, any]) -> Tag:
    # logger.debug(d_item)
    return Tag(
        name=d_item["tag_name_sk"],
        rating=d_item["rating_sk"],
    )


async def get_popular_post_tags(query_dto: TagQueryDTO = None) -> list[Tag]:
    if query_dto is None:
        query_dto = TagQueryDTO()

    return await query_dynamodb_items(
        query_dto=query_dto,
        index_name="TAGS_BY_TYPE_RATING",
        key_condition_expr=Key("tag_type_pk").eq("POST_TAG"),
        map_fn=tag_from_dynamodb,
    )


async def get_post_tags_by_prefix(query_dto: TagQueryDTO = None) -> list[Tag]:
    if query_dto is None:
        query_dto = TagQueryDTO()

    table = await get_dynamodb_table()
    resp = await table.query(
        IndexName="TAGS_BY_TYPE_NAME",
        KeyConditionExpression=Key("tag_type_pk").eq("POST_TAG") & Key("tag_name_sk").begins_with(query_dto.prefix),
        Limit=query_dto.limit
    )
    items = resp.get("Items", [])
    # logger.debug(f"Tags: {items}")
    return [tag_from_dynamodb(item) for item in items]


async def get_post_tags(query_dto: TagQueryDTO = None) -> list[Tag]:
    if query_dto.prefix:
        return await get_post_tags_by_prefix(query_dto)
    return await get_popular_post_tags(query_dto)


async def create_contact_message(message_dto: ContactMessageDTO, user: User = None) -> ContactMessage:
    if user:
        verify_authorization(user, Permission.CREATE_CONTACT_MESSAGE)

    table = await get_dynamodb_table()
    now = utc_now()
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
        "sk": "META",
        "message_id": message_id,
        "name": message_dto.name,
        "email": message_dto.email,
        "message": message_dto.message,
        # todo: rename to created_at
        "created_at_sk": now,
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
        user_token = user_token_from_jwt_claims(claims, token)
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


async def get_latest_active_users(query_dto: UserQueryDTO = None) -> list[User]:
    if query_dto is None:
        query_dto = UserQueryDTO()

    return await query_dynamodb_items(
        query_dto=query_dto,
        index_name="USERS_BY_STATUS_CREATED_AT",
        key_condition_expr=Key("user_status_pk").eq(f"USER#STATUS#{UserStatus.ACTIVE.value}"),
        map_fn=user_from_dynamodb,
    )


async def get_active_users(query_dto: UserQueryDTO = None) -> list[User]:
    if query_dto is None:
        query_dto = PostQueryDTO()
    if query_dto.type == UserQueryType.POPULAR:
        return await get_popular_active_users(query_dto)
    return await get_latest_active_users(query_dto)


def unix_to_month_year(timestamp: int, tz: str | None = None) -> str:
    """
    Convert Unix timestamp to 'Feb 2024' format, optional timezone.
    """
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if tz:
        dt = dt.astimezone(ZoneInfo(tz))
    return dt.strftime("%b %Y")


def unix_to_full_date(timestamp: int, tz: str | None = None) -> str:
    """
    Convert Unix timestamp to 'May 29, 2024' format, optional timezone.
    """
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if tz:
        dt = dt.astimezone(ZoneInfo(tz))
    return dt.strftime("%b %d, %Y")


async def get_latest_published_posts_by_user(user: User, query_dto: PostQueryDTO = None) -> list[Post]:
    if user.published_posts_count == 0:
        return []
    if query_dto is None:
        query_dto = PostQueryDTO()

    return await query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_USER_STATUS_CREATED_AT",
        key_condition_expr=Key("post_user_status_pk").eq(f"POST#USER#{user.id}#STATUS#{PostStatus.PUBLISHED.value}"),
        map_fn=post_from_dynamodb,
    )


async def get_popular_active_users(query_dto: UserQueryDTO = None) -> list[User]:
    if query_dto is None:
        query_dto = UserQueryDTO()

    return await query_dynamodb_items(
        query_dto=query_dto,
        index_name="USERS_BY_STATUS_RATING",
        key_condition_expr=Key("user_status_pk").eq(f"USER#STATUS#{UserStatus.ACTIVE.value}"),
        map_fn=user_from_dynamodb,
    )


def post_impression_from_dynamodb(d_item: dict[str, any]) -> PostImpression:
    user_id = d_item["user_id"]
    return PostImpression(
        owner_id=user_id,
        post_id=d_item["post_id"],
        user_id=user_id,
        action=d_item["action"],
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at")
    )


async def find_post_impression(post: Post, user: User) -> PostImpression | None:
    table = await get_dynamodb_table()
    resp = await table.get_item(
        Key={
            "pk": f"POST#{post.id}",
            "sk": f"IMP#USER#{user.id}"
        }
    )
    item = resp.get("Item")
    if not item:
        return None
    # logger.debug(f"PostImpression: {item}")
    return post_impression_from_dynamodb(item)


async def update_post_impression(post: Post, update_post_impression_dto: UpdatePostImpressionDTO, user: User) -> None:
    verify_authorization(user, Permission.UPDATE_POST_IMPRESSION, post)

    current_impression = await find_post_impression(post, user)
    current_action = current_impression.action if current_impression else None
    action = update_post_impression_dto.action
    post_impression_item = {
        "post_id": post.id,
        "user_id": user.id,
        "action": action,
    }
    transacts = []

    post_key = (f"POST#{post.id}", "META")
    post_imp_key = (f"POST#{post.id}", f"IMP#USER#{user.id}")

    if action == PostImpressionAction.LIKE:
        if current_action == PostImpressionAction.LIKE:
            add_dynamodb_delete_transact(transacts, post_imp_key)
            add_dynamodb_update_transact(transacts, post_key,
                                         deltas={"likes_count": -1, "rating_sk": compute_rating_sk(-1)})
        elif current_action == PostImpressionAction.DISLIKE:
            add_dynamodb_update_transact(transacts, post_imp_key, {"action": PostImpressionAction.LIKE})
            add_dynamodb_update_transact(transacts, post_key, deltas={"dislikes_count": -1, "likes_count": 1,
                                                                      "rating_sk": compute_rating_sk(2)})
        else:
            add_dynamodb_put_transact(transacts, post_imp_key,
                                      {**post_impression_item, "action": PostImpressionAction.LIKE},
                                      new_pk_only=True)
            add_dynamodb_update_transact(transacts, post_key,
                                         deltas={"likes_count": 1, "rating_sk": compute_rating_sk(1)})

    elif action == PostImpressionAction.DISLIKE:
        if current_action == PostImpressionAction.DISLIKE:
            add_dynamodb_delete_transact(transacts, post_imp_key)
            add_dynamodb_update_transact(transacts, post_key,
                                         deltas={"dislikes_count": -1, "rating_sk": compute_rating_sk(1)})
        elif current_action == PostImpressionAction.LIKE:
            add_dynamodb_update_transact(transacts, post_imp_key, {"action": PostImpressionAction.DISLIKE})
            add_dynamodb_update_transact(transacts, post_key, deltas={"likes_count": -1, "dislikes_count": 1,
                                                                      "rating_sk": compute_rating_sk(-2)})
        else:
            add_dynamodb_put_transact(transacts, post_imp_key,
                                      {**post_impression_item, "action": PostImpressionAction.DISLIKE},
                                      new_pk_only=True)
            add_dynamodb_update_transact(transacts, post_key,
                                         deltas={"dislikes_count": 1, "rating_sk": compute_rating_sk(-1)})

    await dynamodb_transact_write(transacts)


async def update_user_impression(
        user: User,
        update_relation_dto: UpdateUserImpressionDTO,
        cur_user: User,
) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER_IMPRESSION, user)

    current_relation = await find_user_impression(user, cur_user)
    current_action = current_relation.action if current_relation else None
    action = update_relation_dto.action
    relation_item = {
        "user_id": cur_user.id,
        "target_user_id": user.id,
        "action": action,
    }
    transacts = []

    user_key = (f"USER#{cur_user.id}", "META")
    target_user_key = (f"USER#{user.id}", "META")
    relation_key = (f"USER#{cur_user.id}", f"REL#USER#{user.id}")

    if action == UserImpressionAction.FOLLOW:
        if current_action == UserImpressionAction.FOLLOW:
            # Unfollow
            add_dynamodb_delete_transact(transacts, relation_key)
            add_dynamodb_update_transact(transacts, user_key, deltas={"following_count": -1})
            add_dynamodb_update_transact(transacts, target_user_key,
                                         deltas={"followers_count": -1, "rating_sk": compute_rating_sk(-1)})
        elif current_action == UserImpressionAction.BLOCK:
            # Switching from block to follow
            add_dynamodb_update_transact(transacts, relation_key, {"action": UserImpressionAction.FOLLOW})
            add_dynamodb_update_transact(transacts, user_key, deltas={"following_count": 1})
            add_dynamodb_update_transact(transacts, target_user_key,
                                         deltas={"followers_count": 1, "rating_sk": compute_rating_sk(2)})
        else:
            # New follow
            add_dynamodb_put_transact(transacts, relation_key, relation_item, new_pk_only=True)
            add_dynamodb_update_transact(transacts, user_key, deltas={"following_count": 1})
            add_dynamodb_update_transact(transacts, target_user_key,
                                         deltas={"followers_count": 1, "rating_sk": compute_rating_sk(1)})

    elif action == UserImpressionAction.BLOCK:
        if current_action == UserImpressionAction.BLOCK:
            # Unblock
            add_dynamodb_delete_transact(transacts, relation_key)
            add_dynamodb_update_transact(transacts, target_user_key, deltas={"rating_sk": compute_rating_sk(1)})
        elif current_action == UserImpressionAction.FOLLOW:
            # Switching from follow to block
            add_dynamodb_update_transact(transacts, relation_key, {"action": UserImpressionAction.BLOCK})
            add_dynamodb_update_transact(transacts, user_key, deltas={"following_count": -1})
            add_dynamodb_update_transact(transacts, target_user_key,
                                         deltas={"followers_count": -1, "rating_sk": compute_rating_sk(-2)})
        else:
            # New block
            add_dynamodb_put_transact(transacts, relation_key, relation_item, new_pk_only=True)
            add_dynamodb_update_transact(transacts, target_user_key, deltas={"rating_sk": compute_rating_sk(-1)})

    await dynamodb_transact_write(transacts)


def enum_to_value(obj):
    if isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, dict):
        return {k: enum_to_value(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [enum_to_value(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(enum_to_value(v) for v in obj)
    else:
        return obj


async def create_dummy_fixtures() -> None:
    if is_prod():
        return
    created_posts = []
    created_users = []
    user_token = get_dummy_user_token()
    root_user = await upsert_user_by_user_token(user_token)
    created_users.append(root_user)
    await update_dynamodb_item((f"USER#{root_user.id}", "META"), {"permissions": [Permission.ROOT]})
    root_user.permissions = [Permission.ROOT]
    update_user_dto = UpdateUserDTO(
        name="John Doe",
        username="j-doe",
        headline="Software Engineer",
        website=HttpUrl("https://example.com"),
        about=("Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the "
               "industry's standard dummy text ever since the 1500s, when an unknown printer took a galley of type "
               "and scrambled it to make a type specimen book. It has survived not only five centuries, but also the "
               "leap into electronic typesetting, remaining essentially unchanged. It was popularised in the 1960s "
               "with the release of Letraset sheets containing Lorem Ipsum passages, and more recently with desktop "
               "publishing software like Aldus PageMaker including versions of Lorem Ipsum."),
        address="1600 Pennsylvania Ave NW, Washington, DC 20500"
    )
    await update_user(root_user, update_user_dto, root_user)
    posts = [
        PostDTO(
            title="Post title #111111111111111111111111",
            content="Post content #111111111111111111111111" * 100,
            tags=["tag1", "tag2", "tag3"]
        ),
        PostDTO(
            title="Post title #22222222222222222222222",
            content="Post content #2222222222222222222222" * 100,
            tags=["tag2", "tag3"]
        ),
        PostDTO(
            title="Post title #3333333333333333333333333",
            content="Post content #333333333333333333333" * 100,
            tags=["tag1", "tag3"]
        ),
    ]
    for post in posts:
        created_post = await create_post(post, root_user)
        await update_post_status(created_post, UpdatePostStatusDTO(status=PostStatus.PUBLISHED), root_user)
        created_posts.append(created_post)
    user_token2 = get_dummy_user_token(sub="p2", email="test2@example.com", name="Some test user")
    user2 = await upsert_user_by_user_token(user_token2)
    created_users.append(user2)
    posts = [
        PostDTO(
            title="Post title #111111111111111111111111 for user 2",
            content="Post content #111111111111111111111111" * 100,
            tags=["tag3"]
        ),
        PostDTO(
            title="Post title #22222222222222222222222 for user 2",
            content="Post content #2222222222222222222222" * 100,
            tags=["tag2"]
        ),
        PostDTO(
            title="Post title #3333333333333333333333333 for user 2",
            content="Post content #333333333333333333333" * 100,
            tags=["tag4"]
        ),
    ]
    for post in posts:
        created_post = await create_post(post, user2)
        await update_post_status(created_post, UpdatePostStatusDTO(status=PostStatus.PUBLISHED), root_user)
        created_posts.append(created_post)
    user_token3 = get_dummy_user_token(sub="p3", email="test3@example.com")
    user3 = await upsert_user_by_user_token(user_token3)
    created_users.append(user3)
    user_token4 = get_dummy_user_token(sub="p4", email="test4@example.com")
    user4 = await upsert_user_by_user_token(user_token4)
    created_users.append(user4)
    for user in created_users:
        for post in created_posts:
            await update_post_impression(post, UpdatePostImpressionDTO(
                action=PostImpressionAction.LIKE if random.random() < .5 else PostImpressionAction.DISLIKE), user)
        for user2 in created_users:
            if user.id != user2.id:
                await update_user_impression(user, UpdateUserImpressionDTO(
                    action=UserImpressionAction.FOLLOW if random.random() < .5 else UserImpressionAction.BLOCK), user2)
