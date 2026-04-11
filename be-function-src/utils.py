import time

INIT_START = time.time()

import re
import os
import uuid
import datetime
import logging
import sys
import httpx
from enum import StrEnum
from urllib.parse import quote
from jose import jwt
from jose.exceptions import JWTError, ExpiredSignatureError
import base64
from typing import Callable, ClassVar, Literal, TypeVar, Any, Union, Optional
from starlette.status import HTTP_200_OK
from jinja2 import Environment, FileSystemLoader, pass_context
import json
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, EmailStr, Field, field_validator, conlist, constr, HttpUrl, computed_field
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from itertools import combinations
from zoneinfo import ZoneInfo
import decimal
from urllib.parse import urlencode
import copy
import filetype
from io import BytesIO
import struct
import random
import html
from urllib.parse import urlparse
import difflib
from html.parser import HTMLParser
import boto3
from functools import lru_cache, partial
import asyncio
import nh3
from dataclasses import dataclass, asdict
from decimal import Decimal

INIT_END = time.time()
print(f"UTILS IMPORTS TIME: {INIT_END - INIT_START:.3f}s")


def to_thread(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


class UserTokenDTO(BaseModel):
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


class UserStatus(StrEnum):
    ACTIVE = "active"
    BANNED = "banned"


@dataclass(slots=True)
class User:
    id: str
    owner_id: str | None
    email: str | None
    avatar_filename: str | None
    name: str
    username: str | None
    github_username: str | None
    headline: str | None
    website: str | None
    address: str | None
    about: str | None
    providers: dict[str, dict[str, str | None]]
    permissions: list[str]
    status: UserStatus
    published_posts_count: int
    unpublished_posts_count: int
    rejected_posts_count: int
    rating: int
    followers_count: int
    following_count: int
    comment: str | None
    post_comments_count: int
    bmc_username: str | None
    redirect_to: str | None
    created_at: int
    updated_at: int | None
    offset: str | None


class FileDTO(BaseModel):
    content: bytes
    filename: str

    @computed_field
    @property
    def size(self) -> int:
        return len(self.content)

    @computed_field
    @property
    def extension(self) -> str | None:
        kind = filetype.guess(self.content)
        if kind:
            return kind.extension
        return None


class ImageFileDTO(FileDTO):
    MAX_IMAGE_SIZE: ClassVar[int] = 2 * 1024 * 1024  # 2 MB
    ALLOWED_IMAGE_EXTENSIONS: ClassVar[set[str]] = {"jpg", "jpeg", "png", "gif"}

    @computed_field
    @property
    def size(self) -> int:
        size = super().size
        if size > self.MAX_IMAGE_SIZE:
            raise ValueError(f"File too large: {size} bytes, max {self.MAX_IMAGE_SIZE}")
        return size

    @computed_field
    @property
    def extension(self) -> str:
        image_type = super().extension
        if image_type not in self.ALLOWED_IMAGE_EXTENSIONS:
            raise ValueError(f"Invalid image type: {image_type}")
        return image_type


class UserDTO(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    username: str | None = Field(None, min_length=3, max_length=30)

    USERNAME_PATTERN: ClassVar[re.Pattern] = re.compile(r"^[a-z0-9-]+$")
    USERNAME_BLACKLIST: ClassVar[set[str]] = {
        "api", "posts", "posts-fragment", "contacts", "post-tags", "users", "users-fragment", "login", "login-callback",
        "logout", "logout-callback", "dummy-fixtures", "me", "privacy-policy", "rules", "terms-of-service",
        "earn-with-us", "popular", "utils",
    }

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str):
        if value is None:
            return value

        value = value.strip()

        if not cls.USERNAME_PATTERN.match(value):
            raise ValueError("Username must contain only lowercase letters, numbers, and hyphens")

        if value.startswith("-") or value.endswith("-"):
            raise ValueError("Username cannot start or end with a hyphen")

        if "--" in value:
            raise ValueError("Username cannot contain consecutive hyphens")

        if value in cls.USERNAME_BLACKLIST:
            raise ValueError("This word can't be a username")

        return value


class UpdateUserDTO(UserDTO):
    avatar_action: Literal["delete", "replace", "keep"] | None = None
    # todo: check if file exists
    avatar_filename: str | None = None
    headline: str | None = Field(None, max_length=150)
    about: str | None = Field(None, max_length=2000)
    website: Optional[HttpUrl] = None
    address: str | None = Field(None, max_length=255)
    github_username: str | None = Field(None, min_length=1, max_length=39)
    bmc_username: str | None = Field(None, min_length=1, max_length=50)

    GITHUB_USERNAME_PATTERN: ClassVar[re.Pattern] = re.compile(r"^[a-zA-Z0-9-]+$")

    @field_validator("github_username")
    @classmethod
    def validate_github_username(cls, value: str | None):
        if value is None:
            return value

        value = value.strip()

        # Allow users to paste full GitHub URL
        if "github.com/" in value:
            value = value.split("github.com/")[-1].strip("/")
            # Handle cases like github.com/user/repo
            value = value.split("/")[0]

        # Normalize
        value = value.lower()

        if not cls.GITHUB_USERNAME_PATTERN.match(value):
            raise ValueError(
                "GitHub username must contain only letters, numbers, and hyphens"
            )

        if value.startswith("-") or value.endswith("-"):
            raise ValueError("GitHub username cannot start or end with a hyphen")

        if "--" in value:
            raise ValueError("GitHub username cannot contain consecutive hyphens")

        return value

    BMC_USERNAME_PATTERN: ClassVar[re.Pattern] = re.compile(r"^[a-zA-Z0-9.]+$")

    @field_validator("bmc_username")
    @classmethod
    def validate_bmc_username(cls, value: str | None):
        if value is None:
            return value

        value = value.strip()

        # Allow users to paste full URL and extract username
        if "buymeacoffee.com/" in value:
            value = value.split("buymeacoffee.com/")[-1].strip("/")

        # Normalize
        value = value.lower()

        # Validate pattern
        if not cls.BMC_USERNAME_PATTERN.match(value):
            raise ValueError(
                "BMC username must contain only letters, numbers, and dots"
            )

        if value.startswith(".") or value.endswith("."):
            raise ValueError("BMC username cannot start or end with a dot")

        if ".." in value:
            raise ValueError("BMC username cannot contain consecutive dots")

        return value


class UpdateUserStatusDTO(BaseModel):
    status: UserStatus = Field(...)
    comment: str = Field(None)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value, info):
        status = info.data.get("status")
        if status == UserStatus.BANNED and not value:
            raise ValueError("Comment is required when banning a user")
        return value


class UserImpressionAction(StrEnum):
    FOLLOW = "follow"
    BLOCK = "block"


@dataclass(slots=True)
class UserImpression:
    owner_id: str
    action: UserImpressionAction
    user_id: str
    target_user_id: str
    created_at: int
    updated_at: int | None


class UpdateUserImpressionDTO(BaseModel):
    action: UserImpressionAction = Field(...)


class ContactMessageDTO(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    message: str = Field(..., min_length=5, max_length=1000)


@dataclass(slots=True)
class ContactMessage:
    id: str
    name: str
    email: str
    message: str
    user_id: str | None
    created_at: int


def sanitize_html(value):
    if not value or not isinstance(value, str):
        return value

    escaped = html.escape(value)
    return escaped.strip()


def sanitize_forbidden_html(value):
    if not value or not isinstance(value, str):
        return value

    cleaned = nh3.clean(
        value,
        tags={
            "h2", "h3", "h4", "h5", "h6",
            "p", "br",
            "b", "strong", "i", "em", "u", "span",
            "ul", "ol", "li",
            "a",
            "img",
            "blockquote",
            "table", "thead", "tbody", "tfoot", "tr", "th", "td",
            "div", "pre", "code",
            "figure", "figcaption",
        },
        attributes={
            "a": {"href", "title", "target"},
            "img": {"src", "alt", "width", "height", "class", "style"},
            "span": {"class"},
            "div": {"class"},
            "table": {"class", "border", "cellpadding", "cellspacing"},
            "th": {"colspan", "rowspan"},
            "td": {"colspan", "rowspan"},
            "figure": {"class"},
            "figcaption": {"class"},
            "code": {"class"},
            "pre": {"class"},
        },
        url_schemes={"http", "https"},
        strip_comments=True,
        link_rel="noopener noreferrer",
    )

    normalized = re.sub(r"<p>\s*</p>", "<br>", cleaned, flags=re.IGNORECASE)
    normalized = re.sub(r"^(?:<br\s*/?>\s*)+", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(?:<br\s*/?>\s*)+$", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.strip()

    return normalized


def sanitize_tags(value):
    if not value:
        return []
    # lowercase, kebab-case, dedupe
    normalized = [to_kebab_case(t) for t in value]
    return list(dict.fromkeys(normalized))


class PostDTO(BaseModel):
    title: str = Field(..., min_length=10, max_length=500)
    content: str = Field(..., min_length=5_000, max_length=50_000)
    tags: conlist(constr(min_length=2, max_length=40), min_length=1, max_length=3)

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        return sanitize_tags(value)


# todo: rename to ReplacePostDTO (?)
class UpdatePostDTO(PostDTO):
    pass


class BaseQueryDTO(BaseModel):
    DEFAULT_OFFSET: ClassVar[str | None] = None
    DEFAULT_LIMIT: ClassVar[int] = 20

    offset: str | None = DEFAULT_OFFSET
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=20)

    def get_dict(self, rewrite: dict[str, Any] | None = None) -> dict[str, Any]:
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


class UserQueryType(StrEnum):
    LATEST = "latest"
    POPULAR = "popular"


class UserQueryDTO(BaseQueryDTO):
    DEFAULT_TYPE: ClassVar[UserQueryType] = UserQueryType.LATEST
    DEFAULT_STATUS: ClassVar[UserStatus] = UserStatus.ACTIVE

    type: UserQueryType = UserQueryType.LATEST
    status: UserStatus = UserStatus.ACTIVE


class TagQueryDTO(BaseQueryDTO):
    prefix: str | None = Field(None, min_length=1, max_length=10)


@dataclass(slots=True)
class Tag:
    name: str
    rating: int
    posts_count: int
    offset: str | None


class PostStatus(StrEnum):
    UNPUBLISHED = "unpublished"
    PUBLISHED = "published"
    REJECTED = "rejected"


class PostQueryType(StrEnum):
    LATEST = "latest"
    POPULAR = "popular"


class PostQueryDTO(BaseQueryDTO):
    DEFAULT_TYPE: ClassVar[PostQueryType] = PostQueryType.LATEST
    DEFAULT_STATUS: ClassVar[PostStatus] = PostStatus.PUBLISHED

    tags: list[str] | None = Field(default_factory=list)  # noqa
    type: PostQueryType = DEFAULT_TYPE
    status: PostStatus = DEFAULT_STATUS


class UpdatePostStatusDTO(BaseModel):
    status: PostStatus = Field(...)
    comment: str = Field(None)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value, info):
        status = info.data.get("status")
        if status == PostStatus.REJECTED and not value:
            raise ValueError("Comment is required when rejecting a post")
        return value


class PostImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class PostImpression:
    owner_id: str
    post_id: str
    action: PostImpressionAction
    user_id: str
    created_at: int
    updated_at: int | None


class UpdatePostImpressionDTO(BaseModel):
    action: PostImpressionAction = Field(...)


@dataclass(slots=True)
class Post:
    id: str
    owner_id: str
    title: str
    slug: str
    user_id: str
    user_slug: str | None
    content: str
    preview: str | None
    tags: list[str]
    status: PostStatus
    comment: str | None
    rating: int
    likes_count: int
    dislikes_count: int
    image_filename: str | None
    redirect_to: str | None
    comments_count: int
    created_at: int
    updated_at: int | None
    published_at: int | None
    is_premium: bool | None
    offset: str | None


@dataclass(slots=True)
class PostComment:
    id: str
    owner_id: str
    user_id: str
    user_name: str | None
    user_avatar_filename: str | None
    user_username: str | None

    def get_user(self) -> User:
        return user_from_dynamodb({
            "id": self.user_id,
            "name": self.user_name,
            "avatar_filename": self.user_avatar_filename,
            "username": self.user_username,
            "created_at": 0,
        })

    post_id: str
    text: str
    rating: int
    likes_count: int
    dislikes_count: int
    replies_count: int
    created_at: int
    updated_at: int | None
    offset: str | None


class PostCommentQueryDTO(BaseQueryDTO):
    pass


class PostCommentDTO(BaseModel):
    text: str = Field(..., min_length=1, max_length=5_000)


class UpdatePostCommentDTO(PostDTO):
    pass


class PostCommentImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class PostCommentImpression:
    owner_id: str
    post_id: str
    action: PostCommentImpressionAction
    user_id: str
    created_at: int
    updated_at: int | None


class UpdatePostCommentImpressionDTO(BaseModel):
    action: PostCommentImpressionAction = Field(...)


class Permission(StrEnum):
    REGULAR = "regular"
    ROOT = "root"
    ALL = "*"

    UPDATE_USER = "update_user"
    UPDATE_USER_STATUS = "update_user_status"
    UPDATE_USER_IMPRESSION = "update_user_impression"
    READ_NON_ACTIVE_USER = "read_non_active_user"

    CREATE_POST = "create_post"
    UPDATE_POST = "update_post"
    UPDATE_POST_STATUS = "update_post_status"
    CREATE_CONTACT_MESSAGE = "create_contact_message"
    UPDATE_POST_IMPRESSION = "toggle_post_impression"
    READ_NON_PUBLISHED_POST = "read_non_published_post"

    CREATE_POST_COMMENT = "create_post_comment"
    UPDATE_POST_COMMENT = "update_post_comment"
    READ_NON_PUBLISHED_POST_COMMENT = "read_non_published_post_comment"

    UTILS_PAGE = "utils_page"
    GENERATE_SITEMAP = "generate_sitemap"


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


class PostByOldSlugRequestedError(Exception):
    def __init__(self, slug: str, post: Post):
        self.slug = slug
        self.post = post


class PostCommentNotFoundError(BaseError):
    pass


class PostCommentNonEditableError(BaseError):
    pass


class UserNotFoundError(BaseError):
    pass


class NotAuthenticatedError(BaseError):
    def __init__(self, message: str = None):
        super().__init__(message=message if message else f"Not authenticated")


class NotAuthorizedError(BaseError):
    def __init__(self, permission: str, message: str = None):
        self.permission = permission
        super().__init__(message=message if message else f"Not authorized: {permission}")


class UserBannedError(BaseError):
    pass


class UserByOldSlugRequestedError(Exception):
    def __init__(self, slug: str, user: User):
        self.slug = slug
        self.user = user


def get_live_config():
    return {
        "app_stage": os.getenv("APP_STAGE"),
        "app_env": os.getenv("APP_ENV"),
        "app_debug": os.getenv("APP_DEBUG"),
        "app_secret": os.getenv("APP_SECRET"),
        "base_url": os.getenv("BASE_URL"),
        "aws_region": os.getenv("AWS_REGION"),
        "dynamodb_endpoint": os.getenv("DYNAMODB_ENDPOINT"),
        "dynamodb_table": os.getenv("DYNAMODB_TABLE"),
        "google_analytics_id": os.getenv("GOOGLE_ANALYTICS_ID"),
        "tinymce_api_key": os.getenv("TINYMCE_API_KEY"),
        "contact_topic_arn": os.getenv("CONTACT_TOPIC_ARN"),
        "allowed_origin": os.getenv("ALLOWED_ORIGIN"),
        "cognito_domain": os.getenv("COGNITO_DOMAIN"),
        "cognito_client_id": os.getenv("COGNITO_CLIENT_ID"),
        "cognito_client_secret": os.getenv("COGNITO_CLIENT_SECRET"),
        "cognito_user_pool_id": os.getenv("COGNITO_USER_POOL_ID"),
        "static_s3_bucket": os.getenv("STATIC_S3_BUCKET"),
        "static_files_dir": os.getenv("STATIC_FILES_DIR", "static"),
        "css_cache_counter": os.getenv("CSS_CACHE_COUNTER", 0),
        "js_cache_counter": os.getenv("JS_CACHE_COUNTER", 0),
        "auth_token_max_age": os.getenv("AUTH_TOKEN_MAX_AGE", 86_400 * 7),
        "auth_jwt_secret": os.getenv("AUTH_JWT_SECRET"),
        "cf_distribution_id": os.getenv("CLOUDFRONT_DISTRIBUTION_ID"),
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
    return config.get("app_stage") == "prod"


def get_config():
    return config


def get_static_files_dir() -> str:
    return config.get("static_files_dir")


def get_static_s3_bucket() -> str:
    return config.get("static_s3_bucket")


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


def get_allowed_origins() -> list[str]:
    return [
        get_config().get("allowed_origin"),
    ]


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


def get_auth_token_max_age() -> int:
    return get_config().get("auth_token_max_age")


def get_auth_jwt_secret() -> str:
    return get_config().get("auth_jwt_secret")


def get_cf_distribution_id() -> str:
    return get_config().get("cf_distribution_id")


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
        permissions: list[str] | None = None,
        hierarchy: dict[str, list[str]] | None = None,
) -> bool:
    """
    Verify if user has access to perform action requiring `permission`.
    """
    hierarchy = hierarchy or get_permission_hierarchy()

    # Owner check
    if resource:
        data = asdict(resource)
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
    raise NotAuthorizedError(permission)


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
    except NotAuthorizedError:
        return False


def to_kebab_case(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


def utc_now() -> int:
    return int(time.time() * 1000)


def dynamodb_transact_write(transacts: list[dict[str, Any]]):
    """
    Executes a DynamoDB TransactWriteItems call and raises a
    DynamoTransactionError with detailed reasons if it fails.
    """
    try:
        get_dynamodb_table().meta.client.transact_write_items(TransactItems=transacts)
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
def jinja2_url(ctx, name: str, **params) -> str:
    request = ctx.get("request")
    if not request:
        raise ValueError("Request not found in context")
    return get_url(request, name, **params)


@pass_context
def jinja2_user_url(ctx, user: User, **params) -> str:
    return get_user_url(ctx.get("request"), user, **params)


def get_user_url(request, user: User, **params) -> str:
    if user.username:
        return get_url(request, "user-by-slug", slug=user.username, **params)
    return get_url(request, "user", user_id=user.id, **params)


@pass_context
def jinja2_post_url(ctx, post: Post, **params) -> str:
    return get_post_url(ctx.get("request"), post, **params)


def get_post_url(request, post: Post, **params) -> str:
    if post.user_slug:
        return get_url(request, "post-by-slugs", user_slug=post.user_slug, post_slug=post.slug, **params)
    return get_url(request, "post", post_id=post.id, **params)


def get_post_comment_url(request, post: Post, post_comment: PostComment, **params) -> str:
    return get_post_url(request, post, **params)


def get_current_url(request) -> str:
    path = request.url.path
    query = request.url.query
    return f"{path}?{query}" if query else path


@pass_context
def jinja2_posts_url(ctx, query: PostQueryDTO | None = None, **params) -> str:
    return get_posts_url(ctx.get("request"), query=query, **params)


def get_posts_url(request, query: PostQueryDTO | None = None, **params) -> str:
    if not query:
        query = PostQueryDTO()

    params = query.get_dict(params)

    slugs: list[str] = []

    type_ = params.pop("type", None)
    if type_:
        type_ = str(type_)
        if type_ != str(PostQueryDTO.DEFAULT_TYPE):
            slugs.append(type_)

    tags = params.pop("tags", None)
    if tags:
        slugs.extend(str(t) for t in tags if t)

    status = params.pop("status", None)
    if status:
        status = str(status)
        if status != str(PostQueryDTO.DEFAULT_STATUS):
            params["status"] = status

    offset = params.pop("offset", None)
    if offset and offset != PostQueryDTO.DEFAULT_OFFSET:
        params["offset"] = offset

    limit = params.pop("limit", None)
    if limit and limit != PostQueryDTO.DEFAULT_LIMIT:
        params["limit"] = limit

    if not slugs:
        return get_url(request, "posts", **params)

    return get_url(request, "posts-by-slugs", slugs_path="/".join(slugs), **params)


def parse_posts_url_slugs_path(slugs_path: str) -> dict:
    data = {}
    slugs = [p for p in slugs_path.split("/") if p]

    if not slugs:
        return {}

    try:
        data["type"] = PostQueryType(slugs[0])
        slugs = slugs[1:]
    except ValueError:
        pass

    data["tags"] = slugs

    return data


@pass_context
def jinja2_users_url(ctx, query: UserQueryDTO | None = None, **params) -> str:
    return get_users_url(ctx.get("request"), query=query, **params)


def get_users_url(request, query: UserQueryDTO | None = None, **params) -> str:
    if not query:
        query = UserQueryDTO()

    params = query.get_dict(params)

    slugs: list[str] = []

    type_ = params.pop("type", None)
    if type_:
        type_ = str(type_)
        if type_ != str(UserQueryDTO.DEFAULT_TYPE):
            slugs.append(type_)

    status = params.pop("status", None)
    if status:
        status = str(status)
        if status != str(UserQueryDTO.DEFAULT_STATUS):
            params["status"] = status

    offset = params.pop("offset", None)
    if offset and offset != UserQueryDTO.DEFAULT_OFFSET:
        params["offset"] = offset

    limit = params.pop("limit", None)
    if limit and limit != UserQueryDTO.DEFAULT_LIMIT:
        params["limit"] = limit

    if not slugs:
        return get_url(request, "users", **params)

    return get_url(request, "users-by-slugs", type=slugs[0], **params)


def get_url(request, name: str, full: bool = False, **params) -> str:
    """
    Generate a URL for a named route.
    By default, returns path-only URLs; set full=True to prepend base_url.
    """
    # Find the route
    route = next(r for r in request.app.routes if getattr(r, "name", None) == name)
    path_param_names = getattr(route, "param_convertors", {}).keys()

    # Split params into path vs query, skipping None
    path_params = {k: v for k, v in params.items() if k in path_param_names and v is not None}
    query_params = {k: v for k, v in params.items() if k not in path_param_names and v is not None}

    # Use request.url_for to get the path
    url_path = request.url_for(name, **path_params).path

    if full and url_path == "/":
        url_path = ""

    # Handle query parameters
    if query_params:
        items = []
        for k, v in query_params.items():
            if isinstance(v, bool):
                v = int(v)
            if isinstance(v, (list, tuple)):
                items.extend((k, int(i) if isinstance(i, bool) else i) for i in v)
            else:
                items.append((k, v))
        if items:
            url_path = f"{url_path}?{urlencode(items)}"

    if full:
        base_url = get_base_url()
        return f"{base_url}{url_path}"

    return url_path


def get_static_url(request, filename, **params) -> str:
    return get_url(request, "user-by-slug", slug=filename, **params)


@pass_context
def jinja2_static_url(ctx, filename, **params) -> str:
    return get_static_url(ctx.get("request"), filename, **params)


def jinja2_col_classes(sizes, inverse: bool = False) -> str:
    """
    Convert:
        3 → 'col col-3'
        {'def': 1, 'sm': 2} → 'col col-1 col-sm-2'

    If inverse=True:
        size -> 12 - size
        e.g. 3 → col-9
    """

    # Allow plain integers → treat as default column size
    if isinstance(sizes, int):
        sizes = {"def": sizes}

    if not isinstance(sizes, dict):
        raise TypeError("jinja2_col_classes expects a dict or int")

    prefixes = {
        "def": "col-",
        "sm": "col-sm-",
        "md": "col-md-",
        "lg": "col-lg-",
        "xl": "col-xl-",
    }

    classes = ["col"]

    for key, value in sizes.items():
        if not isinstance(value, int):
            continue  # ignore bad values

        prefix = prefixes.get(key)
        if prefix is None:
            continue

        final_value = (12 - value) if inverse else value
        classes.append(f"{prefix}{final_value}")

    return " ".join(classes)


def get_jinja2_env():
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    jinja2_env = Environment(
        loader=FileSystemLoader(templates_dir),
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=not is_prod()
    )
    jinja2_env.filters.update({
        "unix_to_month_year": unix_to_month_year,
        "unix_to_full_date": unix_to_full_date,
        "iso_utc": jinja2_iso_utc,
        "col_classes": jinja2_col_classes,
    })
    jinja2_env.globals.update(get_config())
    jinja2_env.globals.update({
        "static_url": jinja2_static_url,
        "url": jinja2_url,
        "user_url": jinja2_user_url,
        "post_url": jinja2_post_url,
        "posts_url": jinja2_posts_url,
        "users_url": jinja2_users_url,
        "Permission": Permission,
        "check_auth": check_authorization,
        "PostStatus": PostStatus,
        "PostImpressionAction": PostImpressionAction,
        "UserImpressionAction": UserImpressionAction,
        "PostQueryType": PostQueryType,
        "UserQueryType": UserQueryType,
        "UserStatus": UserStatus,
        "PostQueryDTO": PostQueryDTO,
        "UserQueryDTO": UserQueryDTO,
    })
    return jinja2_env


jinja2_env = Lazy(get_jinja2_env)

import boto3


@lru_cache
def get_dynamodb_resource():
    args = {} if is_prod() else {
        "region_name": get_aws_region(),
        "endpoint_url": get_dynamodb_endpoint(),
    }
    return boto3.resource("dynamodb", **args)


@lru_cache
def get_dynamodb_table():
    return get_dynamodb_resource().Table(get_dynamodb_table_name())


@lru_cache
def get_s3_client():
    return boto3.client("s3")


@lru_cache
def get_cloudfront_client():
    return boto3.client("cloudfront")


@lru_cache
def get_sns_client():
    return boto3.client("sns")


def invalidate_cloudfront(items: list[str]):
    get_cloudfront_client().create_invalidation(
        DistributionId=get_cf_distribution_id(),
        InvalidationBatch={
            "Paths": {
                "Quantity": len(items),
                "Items": items,
            },
            "CallerReference": str(uuid.uuid4()),
        },
    )


def get_html_content(template: str, data: dict[str, Any]) -> str:
    if data is None:
        data = {}
    template = jinja2_env().get_template(template)
    return template.render(data)


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


def save_public_file(file_dto: FileDTO, filename: str = None) -> str:
    if not filename:
        file_ext = file_dto.extension
        filename = str(uuid.uuid4())
        if isinstance(file_dto, ImageFileDTO):
            try:
                width, height = get_image_dimensions(file_dto.content)
                filename += f"_{width}x{height}"
            except ValueError:
                pass
        filename += f".{file_ext}"

    if not is_prod():
        with open(f"./{get_static_files_dir()}/{filename}", "wb") as f:
            f.write(file_dto.content)
        return filename

    stream = BytesIO(file_dto.content)
    stream.seek(0)

    get_s3_client().upload_fileobj(stream, get_static_s3_bucket(), filename)
    return filename


def drop_public_file(filename: str) -> None:
    if not is_prod():
        path = os.path.join(f"./{get_static_files_dir()}", filename)
        if os.path.exists(path):
            os.remove(path)
        return

    get_s3_client().delete_object(Bucket=get_static_s3_bucket(), Key=filename)


def to_datetime(ts: any) -> datetime:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    if isinstance(ts, Decimal):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)

    if isinstance(ts, str):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)

    raise TypeError(f"Invalid timestamp type: {type(ts)} -> {ts}")


def get_user_by_user_token(token: UserTokenDTO) -> User | None:
    table = get_dynamodb_table()
    provider_user_item = None
    user_item = None
    user_id = None

    # 1: Lookup provider user record
    if token.sub:
        iss = token.iss.split("/")[-1]
        resp = table.get_item(
            Key={
                "pk": f"PROVIDER_USER#{iss}#{token.sub}",
                "sk": "META"
            }
        )
        provider_user_item = resp.get("Item")
        if provider_user_item:
            user_id = provider_user_item["user_id"]

            # Fetch user record
            resp = table.get_item(
                Key={
                    "pk": f"USER#{user_id}",
                    "sk": "META"
                }
            )
            user_item = resp.get("Item")

    # 2: Fallback: lookup user by email
    # todo: user_item instead of provider_user_item (?)
    if not provider_user_item and token.email:
        resp = query_dynamodb_table(
            index_name="USERS_BY_EMAIL",
            key_condition_expr=Key("user_email_pk").eq(token.email),
            limit=1
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

    # Replace invalid characters with hyphen
    username = re.sub(r"[^a-z0-9]+", "-", username)

    # Remove consecutive hyphens
    username = re.sub(r"-{2,}", "-", username)

    # Remove leading/trailing hyphens
    username = username.strip("-").strip()

    if not username:
        return None

    # Append timestamp for uniqueness
    username += f"-{now}"

    return username


def upsert_user_by_user_token(token: UserTokenDTO, status: UserStatus = UserStatus.ACTIVE) -> User:
    now = utc_now()

    user = get_user_by_user_token(token)
    if user:
        user_id = user.id
        providers = user.providers
    else:
        user_id = str(uuid.uuid4())
        providers = {}

    iss = token.iss.split("/")[-1]
    providers[iss] = {"sub": token.sub, "username": token.username, "name": token.name}

    transacts = []

    if user:
        add_dynamodb_update_transact(transacts, (f"USER#{user_id}", "META"), {"providers": providers})
        user.providers = providers
    else:
        name = sanitize_html(build_user_name(token.name, now))
        user_item = {
            "id": user_id,
            "user_email_pk": token.email,
            "name": name,
            "providers": providers,
            "status": status,
            "rating_sk": compute_rating_sk(0, now),
            "created_at": now,
            "user_status_pk": f"USER#{status}",
        }
        username = sanitize_html(build_user_username(token.name, token.username, now))
        if username:
            user_item["username"] = username
            add_dynamodb_put_transact(transacts, (f"USER_SLUG#{username}", "META"), {"user_id": user_id},
                                      new_pk_only=True)
        add_dynamodb_put_transact(transacts, (f"USER#{user_id}", "META"), user_item)
        user = user_from_dynamodb(user_item)

    add_dynamodb_put_transact(transacts, (f"PROVIDER_USER#{iss}#{token.sub}", "META"), {
        "user_id": user_id,
        "email": token.email,
        "created_at": now,
        "updated_at": now
    })

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="username")
        raise

    return user


def user_token_from_jwt_claims(claims: dict[str, Any], plain_token: str | None = None) -> UserTokenDTO:
    exp = to_datetime(claims.get("exp"))
    max_age = None

    if exp is not None:
        now = datetime.now(timezone.utc)
        delta = exp - now
        max_age = max(0, int(delta.total_seconds()))

    return UserTokenDTO(
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
) -> UserTokenDTO:
    return UserTokenDTODTO(
        sub=sub,
        iss=iss,
        username=username,
        email=email,
        name=name,
        iat=None,
        exp=None,
        max_age=None,
        aud="",
        plain_token=encode_offset(dict(locals()))
    )


def get_user_by_auth_token(token: str | None) -> User | None:
    user_token = get_user_token_by_auth_jwt_token(token)

    if user_token is None:
        return None

    # logger.debug(f"user_token: {user_token}")

    user = get_user_by_user_token(user_token)
    # logger.debug(f"user: {user}")

    return user


def get_user_token_by_auth_jwt_token(token: str | None) -> UserTokenDTO | None:
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            get_auth_jwt_secret(),
            algorithms=["HS256"],
            options={"verify_aud": False}
        )

        if payload.get("type") != "auth_token":
            raise InvalidTokenError("Invalid token type")

        return UserTokenDTO(
            sub=payload.get("sub"),
            iss="internal_auth",
            email=payload.get("email"),
            name=payload.get("name"),
            username=payload.get("username"),
            aud=payload.get("aud"),
            iat=to_datetime(payload["iat"]),
            exp=to_datetime(payload["exp"]),
        )

    except ExpiredSignatureError:
        raise InvalidTokenError("Session token expired")

    except JWTError:
        raise InvalidTokenError("Invalid session token")


def post_from_dynamodb(d_item: dict[str, Any]) -> Post:
    owner_id = d_item["user_id"]
    content = d_item["content"]
    return Post(
        id=d_item["id"],
        owner_id=owner_id,
        title=d_item["title"],
        slug=d_item["post_slug"],
        user_id=owner_id,
        content=content,
        preview=d_item.get("preview"),
        tags=d_item.get("tags", []),
        status=d_item["status"],
        comment=d_item.get("comment"),
        rating=d_item["rating_sk"],
        likes_count=d_item.get("likes_count", 0),
        dislikes_count=d_item.get("dislikes_count", 0),
        user_slug=d_item.get("user_slug"),
        image_filename=d_item.get("image_filename"),
        redirect_to=d_item.get("redirect_to"),
        comments_count=d_item.get("comments_count", 0),
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at"),
        published_at=d_item.get("published_at"),
        is_premium=False,
        offset=None,
    )


def post_comment_from_dynamodb(d_item: dict[str, Any]) -> PostComment:
    owner_id = d_item["user_id"]
    return PostComment(
        id=d_item["id"],
        owner_id=owner_id,
        user_id=owner_id,
        user_name=d_item.get("user_name"),
        user_avatar_filename=d_item.get("user_avatar_filename"),
        user_username=d_item.get("user_username"),
        post_id=d_item["post_id"],
        text=d_item["text"],
        rating=d_item.get("rating", 0),
        likes_count=d_item.get("likes_count", 0),
        dislikes_count=d_item.get("dislikes_count", 0),
        replies_count=d_item.get("replies_count", 0),
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at"),
        offset=None,
    )


def compute_rating_sk(rating: int, created_at: int = 0) -> int:
    return rating * 10_000_000_000_000 + created_at


class FirstPExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_p = False
        self.text_parts = []
        self.found = False

    def handle_starttag(self, tag, attrs):
        if tag == "p" and not self.found:
            self.in_p = True

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            self.in_p = False
            self.found = True  # stop after first <p>

    def handle_data(self, data):
        if self.in_p:
            self.text_parts.append(data)


def find_preview(html_content: str) -> str | None:
    parser = FirstPExtractor()
    parser.feed(html_content)

    text = " ".join(part.strip() for part in parser.text_parts if part.strip())

    if not text:
        return None

    return text[:300]


def find_static_image_filename(html_content: str) -> str | None:
    allowed_extensions = "|".join(ImageFileDTO.ALLOWED_IMAGE_EXTENSIONS)

    # Matches filenames generated by save_public_file:
    # static/<uuid>[optional _<width>x<height>].<ext>
    pattern = (
        rf'<img[^>]+src=["\']'
        rf'([0-9a-fA-F-]+(?:_[0-9]+x[0-9]+)?\.'
        rf'(?:{allowed_extensions}))["\']'
    )

    match = re.search(pattern, html_content, flags=re.IGNORECASE)
    if not match:
        return None

    return match.group(1)


def create_post(post_dto: PostDTO, user: User) -> Post:
    verify_authorization(user, Permission.CREATE_POST)

    if user.status == UserStatus.BANNED:
        raise UserBannedError()

    now = utc_now()
    status = PostStatus.UNPUBLISHED
    post_id = str(uuid.uuid4())
    title = sanitize_html(post_dto.title)
    content = sanitize_forbidden_html(post_dto.content)
    preview = find_preview(content)
    image_filename = find_static_image_filename(content)
    tags = sanitize_tags(post_dto.tags)
    slug = to_kebab_case(title)

    transacts = []

    post_item = {
        "id": post_id,
        "title": title,
        "post_slug": slug,
        "user_id": user.id,
        "content": content,
        "tags": tags,
        "rating_sk": compute_rating_sk(0, now),
        "status": status,
        "created_at": now,
        "post_status_pk": f"POST#{status}",
        "post_user_status_pk": f"POST#{user.id}#{status}",
    }
    if preview:
        post_item["preview"] = preview
    if image_filename:
        post_item["image_filename"] = image_filename
    if user.username:
        post_item["user_slug"] = user.username
    add_dynamodb_put_transact(transacts, (f"POST#{post_id}", "META"), post_item, new_pk_only=True)
    add_dynamodb_update_transact(transacts, (f"USER#{user.id}", "META"), deltas={"unpublished_posts_count": 1})
    # todo: should be unique in combination with username (user, post)
    add_dynamodb_put_transact(transacts, (f"POST_SLUG#{slug}", "META"), {"post_id": post_id}, new_pk_only=True)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    return post_from_dynamodb(post_item)


def get_text_diff_percentage(t1, t2) -> int:
    seq = difflib.SequenceMatcher(None, t1, t2)
    similarity = seq.ratio()
    change_percentage = (1 - similarity) * 100
    return int(change_percentage)


def update_post(post: Post, update_post_dto: UpdatePostDTO, cur_user: User) -> None:
    verify_authorization(cur_user, Permission.UPDATE_POST, post)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    old_status = post.status
    published_already = old_status == PostStatus.PUBLISHED
    should_set_status_to_unpublished = False
    now = utc_now()

    changes = update_post_dto.model_dump(exclude_unset=True)
    if not changes:
        return
    for k, v in changes.items():
        if k == "title":
            changes[k] = sanitize_html(v)
        elif k == "content":
            changes[k] = sanitize_forbidden_html(v)
        elif k == "tags":
            changes[k] = sanitize_tags(v)

    transacts = []

    old_title = post.title
    title = changes.get("title", old_title)
    if title != old_title:
        if published_already and get_text_diff_percentage(old_title, title) > 10:
            should_set_status_to_unpublished = True
        old_slug = post.slug
        slug = to_kebab_case(title)
        if old_slug != slug:
            changes["post_slug"] = slug
            # Create redirect item so old slug resolves
            redirect_item = {
                "post_slug": old_slug,
                "redirect_to": slug,
                "created_at": now
            }
            add_dynamodb_put_transact(transacts, (f"POST_REDIRECT#{old_slug}", "META"), redirect_item, new_pk_only=True)
            # Create new slug lock
            add_dynamodb_put_transact(transacts, (f"POST_SLUG#{slug}", "META"), {"post_id": post.id}, new_pk_only=True)

    old_content = post.content
    content = changes.get("content", old_content)
    if content != old_content:
        if published_already and get_text_diff_percentage(old_content, content) > 10:
            should_set_status_to_unpublished = True
        changes["preview"] = find_preview(content)
        changes["image_filename"] = find_static_image_filename(content)

    old_tags = sorted(post.tags)
    tags = sorted(changes.get("tags", old_tags))
    if tags != old_tags:
        if published_already:
            should_set_status_to_unpublished = True

            # Decrease rating for old tags
            removed_tags = set(old_tags) - set(tags)
            for tag in removed_tags:
                transacts.append({
                    "Update": {
                        "TableName": get_dynamodb_table_name(),
                        "Key": {
                            "pk": f"POST_TAG#{tag}",
                            "sk": "META"
                        },
                        "UpdateExpression": (
                            "SET rating_sk = rating_sk - :rating_sk_dec,"
                            "    #posts_count = if_not_exists(#posts_count, :zero) - :dec,"
                            "    updated_at = :now"
                        ),
                        "ExpressionAttributeNames": {
                            "#posts_count": "posts_count",
                        },
                        "ExpressionAttributeValues": {
                            ":rating_sk_dec": compute_rating_sk(1),
                            ":dec": 1,
                            ":now": now,
                            ":zero": 0,
                        }
                    }
                })

            # Remove old tag combos
            for r in range(1, len(old_tags) + 1):
                for combo in combinations(sorted(old_tags), r):
                    post_tag_combo_key = ("POST_TAG_COMBO#" + "#".join(combo), f"POST#{post.created_at}#{post.id}")
                    add_dynamodb_delete_transact(transacts, post_tag_combo_key)

    if published_already and should_set_status_to_unpublished:
        changes["status"] = PostStatus.UNPUBLISHED

    status = changes.get("status", post.status)
    if status != old_status:
        # Update post lists
        changes["post_status_pk"] = f"POST#{status}"
        changes["post_user_status_pk"] = f"POST#{post.user_id}#{status}"

        # User post counters
        owner = find_user(post.user_id)
        if owner:
            deltas = {
                f"{old_status}_posts_count": -1,
                f"{status}_posts_count": 1,
            }
            add_dynamodb_update_transact(transacts, (f"USER#{owner.id}", "META"), deltas=deltas)
            for key, delta in deltas.items():
                setattr(owner, key, getattr(owner, key) + delta)

    add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), changes)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    for key, value in changes.items():
        if key == "post_slug":
            key = "slug"
        if hasattr(post, key):
            setattr(post, key, value)


def find_post(post_id: str) -> Post | None:
    resp = get_dynamodb_table().get_item(
        Key={
            "pk": f"POST#{post_id}",
            "sk": "META"
        }
    )
    item = resp.get("Item")
    if not item:
        return None

    return post_from_dynamodb(item)


def get_post(post_id: str, cur_user: User = None) -> Post:
    post = find_post(post_id)
    if post is None:
        raise PostNotFoundError(f"Post '{post_id}' not found")
    if post.status != PostStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_POST, post)
    return post


def find_post_by_slug(slug: str) -> Post | None:
    resp = query_dynamodb_table(
        index_name="POSTS_BY_SLUG",
        key_condition_expr=Key("post_slug").eq(slug),
        limit=1
    )
    items = resp.get("Items", [])
    if not items:
        return None
    item = items[0]
    # logger.debug(f"Post by slug: {item}")
    return post_from_dynamodb(item)


def find_post_by_slug_follow_redirects(slug: str) -> Post | None:
    visited = set()
    current_slug = slug

    while True:
        if current_slug in visited:
            raise RuntimeError("Redirect loop detected")

        visited.add(current_slug)

        resp = query_dynamodb_table(
            index_name="POSTS_BY_SLUG",
            key_condition_expr=Key("post_slug").eq(current_slug),
            limit=1,
        )

        items = resp.get("Items", [])
        if not items:
            return None

        item = items[0]
        redirect_to = item.get("redirect_to")
        if redirect_to:
            current_slug = redirect_to
            continue

        return post_from_dynamodb(item)


def get_post_by_slugs(user_slug: str, post_slug: str, cur_user: User = None) -> Post:
    post = find_post_by_slug_follow_redirects(post_slug)
    if post is None:
        raise PostNotFoundError(f"Post '{post_slug}' not found")
    if post.user_slug != user_slug:
        raise UserNotFoundError(f"User '{user_slug}' not found")
    if post.status != PostStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_POST, post)
    if post.slug != post_slug:
        raise PostByOldSlugRequestedError(post_slug, post)
    return post


def create_post_comment(post: Post, post_comment_dto: PostCommentDTO, user: User) -> PostComment:
    verify_authorization(user, Permission.CREATE_POST_COMMENT)

    if user.status == UserStatus.BANNED:
        raise UserBannedError()

    now = utc_now()
    comment_id = f"{now}#{str(uuid.uuid4())}"

    transacts = []

    post_comment_item = {
        "id": comment_id,
        "post_id": post.id,
        "user_id": user.id,
        "user_name": user.name,
        "user_avatar_filename": user.avatar_filename,
        "user_username": user.username,
        "text": post_comment_dto.text,
        "created_at": now,
    }

    add_dynamodb_put_transact(transacts, (f"POST#{post.id}", f"COMMENT#{comment_id}"), post_comment_item)
    add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), deltas={"comments_count": 1})
    add_dynamodb_update_transact(transacts, (f"USER#{user.id}", "META"), deltas={"post_comments_count": 1})

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    return post_comment_from_dynamodb(post_comment_item)


def update_post_comment(post: Post, post_comment: PostComment, update_post_comment_dto: UpdatePostCommentDTO,
                        cur_user: User) -> None:
    verify_authorization(cur_user, Permission.UPDATE_POST_COMMENT, post_comment)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    if post_comment.likes_count != 0 or post_comment.dislikes_count != 0:
        raise PostCommentNonEditableError()

    changes = update_post_comment_dto.model_dump(exclude_unset=True)
    if not changes:
        return

    for k, v in changes.items():
        if k == "text":
            changes[k] = sanitize_html(v)

    transacts = []

    add_dynamodb_update_transact(transacts, (f"POST#{post.id}", f"COMMENT{post_comment.id}"), changes)

    dynamodb_transact_write(transacts)

    for key, value in changes.items():
        if hasattr(post_comment, key):
            setattr(post_comment, key, value)


def find_post_comment(post_id: str, post_comment_id: str) -> PostComment | None:
    resp = get_dynamodb_table().get_item(
        Key={
            "pk": f"POST#{post_id}",
            "sk": f"COMMENT#{post_comment_id}"
        }
    )
    item = resp.get("Item")
    return post_comment_from_dynamodb(item) if item else None


def get_post_comment(post_id: str, post_comment_id: str) -> PostComment:
    post_comment = find_post_comment(post_id, post_comment_id)
    if post_comment is None:
        raise PostCommentNotFoundError(f"Post comment '{post_comment_id}' not found")
    return post_comment


def user_from_dynamodb(d_item: dict[str, Any]) -> User:
    owner_id = d_item["id"]
    return User(
        id=owner_id,
        owner_id=owner_id,
        email=d_item.get("user_email_pk"),
        avatar_filename=d_item.get("avatar_filename"),
        name=d_item["name"],
        username=d_item.get("username"),
        github_username=d_item.get("github_username"),
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
        rating=d_item.get("rating_sk", 0),
        followers_count=d_item.get("followers_count", 0),
        following_count=d_item.get("following_count", 0),
        comment=d_item.get("comment"),
        post_comments_count=d_item.get("post_comments_count", 0),
        bmc_username=d_item.get("bmc_username"),
        redirect_to=d_item.get("redirect_to"),
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at"),
        offset=None,
    )


def find_user(user_id: str) -> User | None:
    resp = get_dynamodb_table().get_item(
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


def user_impression_from_dynamodb(d_item: dict[str, Any]) -> UserImpression:
    user_id = d_item["user_id"]
    return UserImpression(
        owner_id=user_id,
        user_id=user_id,
        target_user_id=d_item["target_user_id"],
        action=d_item["action"],
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at")
    )


def find_user_impression(user: User, cur_user: User) -> UserImpression | None:
    resp = get_dynamodb_table().get_item(
        Key={
            "pk": f"USER#{cur_user.id}",
            "sk": f"REL#{user.id}"
        }
    )
    item = resp.get("Item")
    if not item:
        return None
    # logger.debug(f"UserImpression: {item}")
    return user_impression_from_dynamodb(item)


def build_dynamodb_put_item_params(
        key: tuple[str, str],
        values: dict[str, Any] | None = None,
        new_pk_only: bool = False
) -> dict[str, Any]:
    if values is None:
        values = {}
    if not values.get("created_at"):
        values["created_at"] = utc_now()

    pk, sk = key
    params = {
        "TableName": get_dynamodb_table_name(),
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
        values: dict[str, Any] | None = None,
        new_pk_only: bool = False
) -> None:
    param_dict = dict(locals())
    param_dict.pop("transacts", None)
    transacts.append(build_dynamodb_put_item_params(**param_dict))


def build_dynamodb_update_item_params(
        key: tuple[str, str],
        changes: dict[str, Any] | None = None,
        deltas: dict[str, Any] | None = None,
        add_updated_at: bool = True
) -> dict[str, Any]:
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
            "TableName": get_dynamodb_table_name(),
            "Key": {"pk": pk, "sk": sk},
            "UpdateExpression": update_expr,
            "ExpressionAttributeNames": expr_attr_names,
            "ExpressionAttributeValues": expr_attr_values,
        }
    }


def add_dynamodb_update_transact(
        transacts: list,
        key: tuple[str, str],
        changes: dict[str, Any] | None = None,
        deltas: dict[str, Any] | None = None,
        add_updated_at: bool = True
) -> None:
    param_dict = dict(locals())
    param_dict.pop("transacts", None)
    transacts.append(build_dynamodb_update_item_params(**param_dict))


def build_dynamodb_delete_item_params(key: tuple[str, str]) -> dict[str, Any]:
    pk, sk = key

    return {
        "Delete": {
            "TableName": get_dynamodb_table_name(),
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


def update_dynamodb_item(
        key: tuple[str, str],
        changes: dict[str, Any] | None = None,
        deltas: dict[str, Any] | None = None,
        add_updated_at: bool = True
) -> None:
    param_dict = dict(locals())
    update_item_params = build_dynamodb_update_item_params(**param_dict)
    get_dynamodb_table().update_item(**update_item_params["Update"])


def update_user(user: User, update_user_dto: UpdateUserDTO, cur_user: User) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_user_dto.model_dump(exclude_unset=True)
    now = utc_now()

    if not changes:
        return
    for k, v in changes.items():
        changes[k] = sanitize_html(v)

    if changes.get("website"):
        changes["website"] = str(changes["website"]).rstrip("/")

    transacts = []

    avatar_action = changes.pop("avatar_action", "keep")

    if avatar_action == "delete":
        changes["avatar_filename"] = None
    elif avatar_action == "replace":
        pass
    elif avatar_action == "keep":
        changes.pop("avatar_filename", None)

    old_avatar = user.avatar_filename
    old_slug = user.username

    if old_slug:
        if "username" in changes:
            slug = changes["username"]
            if old_slug != slug:
                # Create redirect item so old slug resolves
                redirect_item = {
                    "username": old_slug,
                    "redirect_to": slug,
                    "created_at": now
                }
                add_dynamodb_put_transact(transacts, (f"USER_REDIRECT#{old_slug}", "META"), redirect_item,
                                          new_pk_only=True)
                # Create new slug lock
                add_dynamodb_put_transact(transacts, (f"USER_SLUG#{slug}", "META"), {"user_id": user.id},
                                          new_pk_only=True)
                posts = get_latest_published_posts_by_user(user)
                for post in posts:
                    add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), {"user_slug": slug})
        else:
            add_dynamodb_delete_transact(transacts, (f"USER_SLUG#{old_slug}", "META"))
            posts = get_latest_published_posts_by_user(user)
            for post in posts:
                add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), {"user_slug": None})

    add_dynamodb_update_transact(transacts, (f"USER#{user.id}", "META"), changes)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="username")
        raise

    if old_avatar and avatar_action in {"delete", "replace"}:
        drop_public_file(old_avatar)

    for key, value in changes.items():
        setattr(user, key, value)


def update_user_status(user: User, update_user_status_dto: UpdateUserStatusDTO, cur_user: User) -> None:
    # logger.debug(f"update_user_status: user: {user}, cur_user: {cur_user}")
    verify_authorization(cur_user, Permission.UPDATE_USER_STATUS)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_user_status_dto.model_dump(exclude_unset=True)
    if not changes:
        return
    for k, v in changes.items():
        changes[k] = sanitize_html(v)
    if not "comment" in changes:
        changes["comment"] = None

    status = changes["status"]

    transacts = []

    add_dynamodb_update_transact(transacts, (f"USER#{user.id}", "META"), {
        **changes,
        "user_status_pk": f"USER#{status}",
    })

    # logger.debug(transacts)

    dynamodb_transact_write(transacts)
    user.status = status


def get_user(user_id: str, cur_user: User = None) -> User:
    user = find_user(user_id)
    if user is None:
        raise UserNotFoundError(f"User '{user_id}' not found")
    if user.status != UserStatus.ACTIVE:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_ACTIVE_USER, user)
    return user


def find_user_by_username(username: str) -> User | None:
    resp = query_dynamodb_table(
        index_name="USERS_BY_USERNAME",
        key_condition_expr=Key("username").eq(username),
        limit=1
    )
    items = resp.get("Items", [])
    if not items:
        return None
    item = items[0]
    # logger.debug(f"User: {item}")
    return user_from_dynamodb(item)


def find_user_by_username_follow_redirects(slug: str) -> User | None:
    visited = set()
    current_slug = slug

    while True:
        if current_slug in visited:
            raise RuntimeError("Redirect loop detected")

        visited.add(current_slug)

        resp = query_dynamodb_table(
            index_name="USERS_BY_USERNAME",
            key_condition_expr=Key("username").eq(current_slug),
            limit=1,
        )

        items = resp.get("Items", [])
        if not items:
            return None

        item = items[0]
        redirect_to = item.get("redirect_to")
        if redirect_to:
            current_slug = redirect_to
            continue

        return user_from_dynamodb(item)


def get_user_by_slug(username: str, cur_user: User = None) -> User:
    user = find_user_by_username_follow_redirects(username)
    if user is None:
        raise UserNotFoundError(f"User '{username}' not found")
    if user.status != UserStatus.ACTIVE:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_ACTIVE_USER, user)
    if user.username != username:
        raise UserByOldSlugRequestedError(username, user)
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


def get_posts(query_dto: PostQueryDTO = None, cur_user: User = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()
    if query_dto.type == PostQueryType.POPULAR:
        if query_dto.tags:
            return get_popular_posts_by_tags(query_dto, cur_user)
        return get_popular_posts(query_dto, cur_user)
    if query_dto.tags:
        return get_latest_posts_by_tags(query_dto, cur_user)
    return get_latest_posts(query_dto, cur_user)


def query_dynamodb_table(
        index_name: str | None = None,
        key_condition_expr: Optional[Key] = None,
        scan_index_forward: bool | None = None,
        limit: int | None = None,
        exclusive_start_key: dict | None = None,
) -> dict[str, Any]:
    query_args: dict[str, Any] = {}
    if index_name is not None:
        query_args["IndexName"] = index_name
    if key_condition_expr is not None:
        query_args["KeyConditionExpression"] = key_condition_expr
    if scan_index_forward is not None:
        query_args["ScanIndexForward"] = scan_index_forward
    if limit is not None:
        query_args["Limit"] = limit
    if exclusive_start_key is not None:
        query_args["ExclusiveStartKey"] = exclusive_start_key

    try:
        return get_dynamodb_table().query(**query_args)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        # Happens if the index doesn't exist yet (e.g., table is empty)
        if error_code == "ValidationException":
            logger.warning(f"DynamoDB index '{index_name}' not ready or empty. Returning empty list.")
            return {}
        raise


T = TypeVar("T")


def query_dynamodb_items(
        query_dto: BaseQueryDTO,
        map_fn: Callable[[dict], T],
        index_name: str | None = None,
        key_condition_expr: Optional[Key] = None,
) -> list[T]:
    """Generic DynamoDB query executor with pagination and mapping."""
    resp = query_dynamodb_table(
        index_name=index_name,
        key_condition_expr=key_condition_expr,
        scan_index_forward=False,
        limit=query_dto.limit,
        exclusive_start_key=decode_offset(query_dto.offset) if query_dto.offset else None
    )

    items = resp.get("Items", [])
    results = [map_fn(item) for item in items]

    # Handle pagination
    if len(results) == query_dto.limit:
        results[-1].offset = encode_offset(resp.get("LastEvaluatedKey"))

    return results


def get_latest_posts(query_dto: PostQueryDTO = None, cur_user: User = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()

    if query_dto.status != PostStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_POST)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_STATUS_CREATED_AT_2",
        key_condition_expr=Key("post_status_pk").eq(f"POST#{query_dto.status}"),
        map_fn=post_from_dynamodb,
    )


def get_latest_published_posts(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[Post]:
    query_dto = PostQueryDTO(limit=limit)
    return get_latest_posts(query_dto)


def get_popular_posts(query_dto: PostQueryDTO = None, cur_user: User = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()

    if query_dto.status != PostStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_POST)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_STATUS_RATING",
        key_condition_expr=Key("post_status_pk").eq(f"POST#{query_dto.status}"),
        map_fn=post_from_dynamodb,
    )


def should_show_popular_posts(latest_posts: list[Post], popular_posts: list[Post]) -> bool:
    """
    Show popular posts only if popular_posts differ from latest_posts.
    Comparison is based on post IDs.
    """
    latest_ids = [post.id for post in latest_posts]
    popular_ids = [post.id for post in popular_posts]

    # Show popular posts only if the lists are not exactly equal
    return latest_ids != popular_ids


def get_popular_published_posts(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[Post]:
    query_dto = PostQueryDTO(limit=limit)
    return get_popular_posts(query_dto)


def get_latest_posts_by_tags(query_dto: PostQueryDTO = None, cur_user: User = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()
    if not query_dto.tags:
        return get_latest_posts(query_dto, cur_user)

    table = get_dynamodb_table()
    query_args = {
        "KeyConditionExpression": Key("pk").eq("POST_TAG_COMBO#" + "#".join(sorted(query_dto.tags))),
        "ScanIndexForward": False,
        "Limit": query_dto.limit,
    }
    if query_dto.offset:
        query_args["ExclusiveStartKey"] = decode_offset(query_dto.offset)
    resp = table.query(**query_args)
    combo_items = resp.get("Items", [])
    # logger.debug(combo_items)
    if not combo_items:
        return []

    # Batch get post metadata
    post_ids = set([item["post_id"] for item in combo_items])
    keys = [{"pk": f"POST#{post_id}", "sk": "META"} for post_id in post_ids]
    resp = table.meta.client.batch_get_item(RequestItems={table.name: {"Keys": keys}})
    post_items = resp["Responses"].get(table.name, [])

    # Maintain original order
    post_items_map = {item["id"]: item for item in post_items}
    ordered_posts = [post_items_map[pid] for pid in post_ids if pid in post_items_map]

    posts = [post_from_dynamodb(item) for item in ordered_posts]
    if len(posts) == query_dto.limit:
        posts[-1].offset = encode_offset(resp.get("LastEvaluatedKey"))
    return posts


def get_post_related_posts(post: Post) -> list[Post]:
    query_dto = PostQueryDTO()
    query_dto.tags = post.tags
    posts = get_popular_posts_by_tags(query_dto)
    return [p for p in posts if p.id != post.id]


def get_post_comments(post: Post, query_dto: PostCommentQueryDTO | None = None) -> list[PostComment]:
    if post.comments_count == 0:
        return []
    if query_dto is None:
        query_dto = PostCommentQueryDTO()

    return query_dynamodb_items(
        query_dto=query_dto,
        key_condition_expr=Key("pk").eq(f"POST#{post.id}") & Key('sk').begins_with(f"COMMENT#"),
        map_fn=post_comment_from_dynamodb,
    )


def get_popular_posts_by_tags(query_dto: PostQueryDTO = None, cur_user: User = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()

    # Increase limit to fetch more posts before filtering
    query_dto_copy = copy.copy(query_dto)
    query_dto_copy.limit = max(query_dto.limit * 5, 100)

    posts = get_popular_posts(query_dto_copy, cur_user)

    if not query_dto.tags:
        return posts

    offset = posts[-1].offset if posts else None

    # Filter by tags
    filtered_posts = [post for post in posts if set(query_dto.tags).issubset(set(post.tags))]
    if filtered_posts:
        filtered_posts[-1].offset = offset

    return filtered_posts


def update_post_status(post: Post, update_post_status_dto: UpdatePostStatusDTO, cur_user: User) -> None:
    # logger.debug(f"update_post_status: post: {post}, cur_user: {cur_user}")
    verify_authorization(cur_user, Permission.UPDATE_POST_STATUS)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    if post.status == PostStatus.PUBLISHED:
        raise PostAlreadyPublishedError()

    changes = update_post_status_dto.model_dump(exclude_unset=True)
    if not changes:
        return
    for k, v in changes.items():
        changes[k] = sanitize_html(v)
    if not "comment" in changes:
        changes["comment"] = None

    old_status = post.status
    status = changes["status"]
    now = utc_now()

    transacts = []

    # User post counters
    owner = find_user(post.user_id)
    if owner:
        deltas = {
            f"{old_status}_posts_count": -1,
            f"{status}_posts_count": 1,
        }
        add_dynamodb_update_transact(transacts, (f"USER#{owner.id}", "META"), deltas=deltas)
        for key, delta in deltas.items():
            setattr(owner, key, getattr(owner, key) + delta)

    if status == PostStatus.PUBLISHED:
        if not post.published_at:
            changes["published_at"] = now
        if owner:
            changes["user_slug"] = owner.username
        # Upsert tags
        for tag in post.tags:
            transacts.append({
                "Update": {
                    "TableName": get_dynamodb_table_name(),
                    "Key": {
                        "pk": f"POST_TAG#{tag}",
                        "sk": "META"
                    },
                    "UpdateExpression": (
                        "SET #new_tag_name_sk = if_not_exists(#new_tag_name_sk, :tag_name_sk), "
                        "    #new_tag_type_pk = if_not_exists(#new_tag_type_pk, :tag_type_pk), "
                        "    #new_rating_sk = if_not_exists(#new_rating_sk, :def_rating_sk) + :rating_sk_inc, "
                        "    #posts_count = if_not_exists(#posts_count, :zero) + :inc, "
                        "    #new_created_at = if_not_exists(#new_created_at, :now), "
                        "    #new_updated_at = :now "
                    ),
                    "ExpressionAttributeNames": {
                        "#new_tag_name_sk": "tag_name_sk",
                        "#new_tag_type_pk": "tag_type_pk",
                        "#new_rating_sk": "rating_sk",
                        "#posts_count": "posts_count",
                        "#new_created_at": "created_at",
                        "#new_updated_at": "updated_at",
                    },
                    "ExpressionAttributeValues": {
                        ":tag_name_sk": tag,
                        ":tag_type_pk": "POST_TAG",
                        ":now": now,
                        ":def_rating_sk": compute_rating_sk(0, now),
                        ":rating_sk_inc": compute_rating_sk(1),
                        ":zero": 0,
                        ":inc": 1,
                    }
                }
            })

        # Create post tag combos
        for r in range(1, len(post.tags) + 1):
            for combo in combinations(sorted(post.tags), r):
                post_tag_combo_key = ("POST_TAG_COMBO#" + "#".join(combo), f"POST#{post.created_at}#{post.id}")
                add_dynamodb_put_transact(transacts, post_tag_combo_key, {"post_id": post.id})

    add_dynamodb_update_transact(transacts, (f"POST#{post.id}", "META"), {
        **changes,
        "post_status_pk": f"POST#{status}",
        "post_user_status_pk": f"POST#{post.user_id}#{status}",
    })

    # logger.debug(transacts)

    dynamodb_transact_write(transacts)
    post.status = status


def tag_from_dynamodb(d_item: dict[str, Any]) -> Tag:
    # logger.debug(d_item)
    return Tag(
        name=d_item["tag_name_sk"],
        rating=d_item["rating_sk"],
        posts_count=d_item.get("posts_count", 0),
        offset=None,
    )


def get_popular_post_tags(query_dto: TagQueryDTO = None) -> list[Tag]:
    if query_dto is None:
        query_dto = TagQueryDTO()

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="TAGS_BY_TYPE_RATING",
        key_condition_expr=Key("tag_type_pk").eq("POST_TAG"),
        map_fn=tag_from_dynamodb,
    )


def get_post_tags_by_prefix(query_dto: TagQueryDTO = None) -> list[Tag]:
    if query_dto is None:
        query_dto = TagQueryDTO()
    resp = query_dynamodb_table(
        index_name="TAGS_BY_TYPE_NAME",
        key_condition_expr=Key("tag_type_pk").eq("POST_TAG") & Key("tag_name_sk").begins_with(query_dto.prefix),
        limit=query_dto.limit
    )
    items = resp.get("Items", [])
    # logger.debug(f"Tags: {items}")
    return [tag_from_dynamodb(item) for item in items]


def get_post_tags(query_dto: TagQueryDTO = None) -> list[Tag]:
    if query_dto.prefix:
        return get_post_tags_by_prefix(query_dto)
    return get_popular_post_tags(query_dto)


def create_contact_message(message_dto: ContactMessageDTO, user: User = None) -> ContactMessage:
    if user:
        verify_authorization(user, Permission.CREATE_CONTACT_MESSAGE)

    now = utc_now()
    message_id = str(uuid.uuid4())

    name = sanitize_html(message_dto.name)
    message = sanitize_html(message_dto.message)

    if is_prod():
        text = (
            f"New contact form submission:\n"
            f"ID: {message_id}\n"
            f"Name: {name}\n"
            f"Email: {message_dto.email}\n"
            f"Message: {message}\n"
            f"User ID: {user.id if user else 'N/A'}"
        )
        get_sns_client().publish(
            TopicArn=get_contact_topic_arn(),
            Message=text,
            Subject="New Contact Form Submission"
        )

    message_item = {
        "pk": f"CONTACT_MESSAGE#{message_id}",
        "sk": "META",
        "message_id": message_id,
        "name": name,
        "email": message_dto.email,
        "message": message,
        "created_at": now,
    }
    if user:
        message_item["user_id"] = user.id

    get_dynamodb_table().put_item(Item=message_item)

    return ContactMessage(
        id=message_id,
        name=message_item["name"],
        email=str(message_item["email"]),
        message=message_item["message"],
        user_id=message_item.get("user_id"),
        created_at=now,
    )


def get_login_redirect_url(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/oauth2/authorize"
            f"?client_id={get_cognito_client_id()}"
            f"&response_type=code"
            f"&redirect_uri={quote(callback_url, safe='')}"
            f"&scope=openid+email+profile"
        )

    return callback_url


def get_user_token_by_code(code: str, callback_url: str) -> UserTokenDTO:
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
            "redirect_uri": callback_url,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + base64.b64encode(
                f"{cognito_client_id}:{cognito_client_secret}".encode()
            ).decode()
        }

        with httpx.Client() as client:
            token_resp = client.post(token_url, data=data, headers=headers)
            if token_resp.status_code != HTTP_200_OK:
                logger.error(f"Token exchange failed: {token_resp.status_code} {token_resp.text}")
                raise CodeExchangeFailedError("Failed to exchange code")
            tokens = token_resp.json()
            # logger.debug(f"Cognito token response: {tokens}")

        id_token = tokens.get("id_token")
        if not id_token:
            raise InvalidTokenError("Missing id_token in Cognito response")
        claims = jwt.get_unverified_claims(id_token)
        if claims.get("token_use") != "id":
            raise InvalidTokenError(f"Unexpected token_use: {claims.get('token_use')}")

        tokens = {"id_token": id_token}
        user_token = user_token_from_jwt_claims(claims, encode_offset(tokens))
    else:
        token_args = decode_offset(code) if code else {}
        user_token = get_dummy_user_token(**token_args)

    upsert_user_by_user_token(user_token)
    return user_token


def create_auth_jwt_token(token: UserTokenDTO) -> str:
    expires_in = get_auth_token_max_age()

    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_in)

    return jwt.encode(
        claims={
            "sub": token.sub,
            "iss": "internal_auth",
            "origin_iss": token.iss,
            "sid": uuid.uuid4().hex,
            "email": token.email,
            "name": token.name,
            "username": token.username,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
            "type": "auth_token",
            "aud": "blog",
            "origin_aud": token.aud,
        },
        key=get_auth_jwt_secret(),
        algorithm="HS256"
    )


def get_logout_redirect_url(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/logout"
            f"?client_id={get_cognito_client_id()}"
            # f"&response_type=code"
            f"&logout_uri={quote(callback_url, safe='')}"
            # f"&scope=openid+email+profile"
        )

    return callback_url


def get_redirect_url(request) -> str:
    redirect_url = request.query_params.get("redirect_url")

    if not redirect_url:
        referer = request.headers.get("referer")
        if referer:
            parsed = urlparse(referer)
            base_url = urlparse(get_base_url())

            # If referer has no netloc (relative path) → safe
            # If referer belongs to your domain → safe
            if not parsed.netloc or parsed.netloc == base_url.netloc:
                redirect_url = referer

    if not redirect_url:
        redirect_url = get_url(request, "index")

    return redirect_url


def get_latest_users(query_dto: UserQueryDTO = None, cur_user: User = None) -> list[User]:
    if query_dto is None:
        query_dto = UserQueryDTO()

    if query_dto.status != UserStatus.ACTIVE:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_ACTIVE_USER)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="USERS_BY_STATUS_CREATED_AT_2",
        key_condition_expr=Key("user_status_pk").eq(f"USER#{query_dto.status}"),
        map_fn=user_from_dynamodb,
    )


def get_users(query_dto: UserQueryDTO = None, cur_user: User = None) -> list[User]:
    if query_dto is None:
        query_dto = PostQueryDTO()
    if query_dto.type == UserQueryType.POPULAR:
        return get_popular_users(query_dto, cur_user)
    return get_latest_users(query_dto, cur_user)


def unix_to_month_year(timestamp: int, tz: str | None = None) -> str:
    """
    Convert Unix timestamp to 'Feb 2024' format, optional timezone.
    """
    dt = to_datetime(timestamp)
    if tz:
        dt = dt.astimezone(ZoneInfo(tz))
    return dt.strftime("%b %Y")


def unix_to_full_date(timestamp: int, tz: str | None = None) -> str:
    """
    Convert Unix timestamp to 'Mar 14' or 'Mar 14, 2025' format.
    If the date is in the current year, omit the year.
    Optionally convert to a specific timezone.
    """
    dt = to_datetime(timestamp)
    if tz:
        dt = dt.astimezone(ZoneInfo(tz))

    now = datetime.now(dt.tzinfo)
    if dt.year == now.year:
        return dt.strftime("%b %d")  # e.g., "Mar 14"
    return dt.strftime("%b %d, %Y")  # e.g., "Mar 14, 2025"


def jinja2_iso_utc(timestamp_ms: int) -> str:
    dt = to_datetime(timestamp_ms / 1000)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_latest_published_posts_by_user(user: User) -> list[Post]:
    return get_latest_posts_by_user(user)


def get_latest_posts_by_user(user: User, query_dto: PostQueryDTO = None, cur_user: User = None) -> list[Post]:
    if query_dto is None:
        query_dto = PostQueryDTO()

    if query_dto.status != PostStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_POST, user)

    if getattr(user, f"{query_dto.status}_posts_count") == 0:
        return []

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_USER_STATUS_CREATED_AT_2",
        key_condition_expr=Key("post_user_status_pk").eq(f"POST#{user.id}#{query_dto.status}"),
        map_fn=post_from_dynamodb,
    )


def get_popular_users(query_dto: UserQueryDTO = None, cur_user: User = None) -> list[User]:
    if query_dto is None:
        query_dto = UserQueryDTO()

    if query_dto.status != UserStatus.ACTIVE:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_ACTIVE_USER)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="USERS_BY_STATUS_RATING",
        key_condition_expr=Key("user_status_pk").eq(f"USER#{query_dto.status}"),
        map_fn=user_from_dynamodb,
    )


def get_popular_active_users(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[User]:
    query_dto = UserQueryDTO(limit=limit)
    return get_popular_users(query_dto)


def post_impression_from_dynamodb(d_item: dict[str, Any]) -> PostImpression:
    user_id = d_item["user_id"]
    return PostImpression(
        owner_id=user_id,
        post_id=d_item["post_id"],
        user_id=user_id,
        action=d_item["action"],
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at")
    )


def find_post_impression(post: Post, user: User) -> PostImpression | None:
    resp = get_dynamodb_table().get_item(
        Key={
            "pk": f"POST#{post.id}",
            "sk": f"IMP#{user.id}"
        }
    )
    item = resp.get("Item")
    if not item:
        return None
    # logger.debug(f"PostImpression: {item}")
    return post_impression_from_dynamodb(item)


def update_post_impression(post: Post, update_post_impression_dto: UpdatePostImpressionDTO, user: User) -> None:
    verify_authorization(user, Permission.UPDATE_POST_IMPRESSION, post)

    if user.status == UserStatus.BANNED:
        raise UserBannedError()

    current_impression = find_post_impression(post, user)
    current_action = current_impression.action if current_impression else None
    action = update_post_impression_dto.action
    post_impression_item = {
        "post_id": post.id,
        "user_id": user.id,
        "action": action,
    }
    transacts = []

    post_key = (f"POST#{post.id}", "META")
    post_imp_key = (f"POST#{post.id}", f"IMP#{user.id}")

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

    dynamodb_transact_write(transacts)


def update_user_impression(
        user: User,
        update_relation_dto: UpdateUserImpressionDTO,
        cur_user: User,
) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER_IMPRESSION, user)

    if user.status == UserStatus.BANNED:
        raise UserBannedError()

    current_relation = find_user_impression(user, cur_user)
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
    relation_key = (f"USER#{cur_user.id}", f"REL#{user.id}")

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

    dynamodb_transact_write(transacts)


def enum_to_value(obj):
    if isinstance(obj, StrEnum):
        return obj
    elif isinstance(obj, dict):
        return {k: enum_to_value(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [enum_to_value(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(enum_to_value(v) for v in obj)
    else:
        return obj


def safe_execute(label: str, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"{label} failed: {e}")
        return None


def generate_sitemap(user: User, request) -> tuple[int, str]:
    verify_authorization(user, Permission.GENERATE_SITEMAP)

    today = datetime.utcnow().date().isoformat()

    def lastmod(ts_ms) -> str:
        if not ts_ms:
            return today
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()

    urls = []

    # Static
    def url(route: str) -> str:
        return get_url(request, route, True)

    urls.extend([
        (url("index"), today),
        (url("contacts"), today),
        (url("rules"), today),
        (url("terms"), today),
        (url("earn"), today),
    ])

    # Post lists
    def posts_url(tp: PostQueryType, tg: Tag | None = None) -> str:
        return get_posts_url(request, type=tp, tags=[tg.name] if tg else [], full=True)

    for type_ in PostQueryType:
        urls.append((posts_url(type_), today))
        for tag in get_post_tags(TagQueryDTO.model_construct(limit=1000)):
            if tag.posts_count > 0:
                urls.append((posts_url(type_, tag), today))

    # Posts
    def post_url(post: Post) -> str:
        return get_post_url(request, post, full=True)

    offset = None
    while posts := get_latest_posts(
            PostQueryDTO.model_construct(status=PostStatus.PUBLISHED, limit=1000, offset=offset)):
        urls.extend([(post_url(post), lastmod(post.updated_at)) for post in posts])
        offset = posts[-1].offset
        if not offset:
            break

    # User lists
    def users_url(tp: UserQueryType) -> str:
        return get_users_url(request, type=tp, full=True)

    for type_ in UserQueryType:
        urls.append((users_url(type_), today))

    # Users
    def user_url(user_: User) -> str:
        return get_user_url(request, user_, full=True)

    offset = None
    while users := get_latest_users(
            UserQueryDTO.model_construct(status=UserStatus.ACTIVE, limit=1000, offset=offset)):
        urls.extend([(user_url(user), lastmod(user.updated_at)) for user in users])
        offset = users[-1].offset
        if not offset:
            break

    # Save
    sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{''.join([f"""<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>""" for (loc, lastmod) in urls])}</urlset>"""

    sitemap_filename = save_public_file(
        FileDTO.model_construct(content=sitemap_xml.encode("utf-8")),
        filename="sitemap.xml",
    )
    sitemap_url = get_static_url(request, sitemap_filename, full=True)

    # Invalidate CDN cache
    if is_prod():
        safe_execute("CF invalidation", invalidate_cloudfront, ["/sitemap.xml"])

    # Notify engines
    if is_prod():
        with httpx.Client(timeout=5.0) as client:
            safe_execute("Google SM notify", client.get, "https://www.google.com/ping", params={"sitemap": sitemap_url})
            safe_execute("Bing SM notify", client.get, "https://www.bing.com/ping", params={"sitemap": sitemap_url})

    return len(urls), sitemap_url


def create_dummy_fixtures() -> None:
    if is_prod():
        return
    created_posts = []
    created_users = []
    user_token = get_dummy_user_token()
    root_user = upsert_user_by_user_token(user_token)
    created_users.append(root_user)
    update_dynamodb_item((f"USER#{root_user.id}", "META"), {"permissions": [Permission.ROOT]})
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
    update_user(root_user, update_user_dto, root_user)
    posts = [
        PostDTO(
            title="Post title #111111111111111111111111",
            content="Post content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        PostDTO(
            title="Post title #22222222222222222222222",
            content="Post content #222222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        PostDTO(
            title="Post title #3333333333333333333333333",
            content="Post content #33333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for post in posts:
        created_post = create_post(post, root_user)
        update_post_status(created_post, UpdatePostStatusDTO(status=PostStatus.PUBLISHED), root_user)
        created_posts.append(created_post)
    user_token2 = get_dummy_user_token(sub="p2", email="test2@example.com", name="Some test user")
    user2 = upsert_user_by_user_token(user_token2)
    created_users.append(user2)
    posts = [
        PostDTO(
            title="Post title #111111111111111111111111 for user 2",
            content="Post content #111111111111111111111111" * 150,
            tags=["tag3"]
        ),
        PostDTO(
            title="Post title #22222222222222222222222 for user 2",
            content="Post content #222222222222222222222222" * 150,
            tags=["tag2"]
        ),
        PostDTO(
            title="Post title #3333333333333333333333333 for user 2",
            content="Post content #33333333333333333333333" * 150,
            tags=["tag4"]
        ),
    ]
    for post in posts:
        created_post = create_post(post, user2)
        update_post_status(created_post, UpdatePostStatusDTO(status=PostStatus.PUBLISHED), root_user)
        created_posts.append(created_post)
    user_token3 = get_dummy_user_token(sub="p3", email="test3@example.com")
    user3 = upsert_user_by_user_token(user_token3)
    created_users.append(user3)
    user_token4 = get_dummy_user_token(sub="p4", email="test4@example.com")
    user4 = upsert_user_by_user_token(user_token4)
    created_users.append(user4)
    for user in created_users:
        for post in created_posts:
            update_post_impression(post, UpdatePostImpressionDTO(
                action=PostImpressionAction.LIKE if random.random() < .5 else PostImpressionAction.DISLIKE), user)
        for user2 in created_users:
            if user.id != user2.id:
                update_user_impression(user, UpdateUserImpressionDTO(
                    action=UserImpressionAction.FOLLOW if random.random() < .5 else UserImpressionAction.BLOCK), user2)
    unpublished_posts = [
        PostDTO(
            title="Unpublished Post title #111111111111111111111111",
            content="Post content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        PostDTO(
            title="Unpublished Post title #22222222222222222222222",
            content="Post content #2222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        PostDTO(
            title="Unpublished Post title #3333333333333333333333333",
            content="Post content #333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for post in unpublished_posts:
        create_post(post, user2)
    rejected_posts = [
        PostDTO(
            title="Rejected Post title #111111111111111111111111",
            content="Post content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        PostDTO(
            title="Rejected Post title #22222222222222222222222",
            content="Post content #2222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        PostDTO(
            title="Rejected Post title #3333333333333333333333333",
            content="Post content #333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for post in rejected_posts:
        created_post = create_post(post, user3)
        update_post_status(created_post,
                           UpdatePostStatusDTO(status=PostStatus.REJECTED, comment="Some rejection reason"),
                           root_user)
