import asyncio
import base64
import copy
import datetime
import html
import json
import logging
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache, partial
from html import unescape
from typing import Callable, TypeVar, Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, pass_context, select_autoescape

from api_route_metadata import API_URL_ROUTES
from article_dtos import (ArticleCommentImpressionAction, ArticleImpressionAction)
from article_tag_subscription_dtos import ArticleTagSubscription
from basic_dtos import UserTokenDTO
from query_dtos import (BaseQueryDTO, ArticleCommentQueryDTO, ArticleQueryDTO, ArticleQueryType, ArticleStatus,
                        ArticleTagQueryDTO, UserQueryDTO, UserQueryType, UserStatus)
from user_dtos import UserImpressionAction


def Key(*args, **kwargs):
    from boto3.dynamodb.conditions import Key as DynamoDBKey
    return DynamoDBKey(*args, **kwargs)


def is_aws_client_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "ClientError" and hasattr(exc, "response")


def to_thread(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


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
    published_articles_count: int
    unpublished_articles_count: int
    rejected_articles_count: int
    rating: int
    followers_count: int
    following_count: int
    comment: str | None
    article_comments_count: int
    article_tag_subscriptions_count: int
    bmc_username: str | None
    redirect_to: str | None
    created_at: int
    updated_at: int | None
    offset: str | None
    show_activity_calendar: bool
    show_recent_activity: bool
    show_interests: bool


@dataclass(slots=True)
class UserActivity:
    id: str
    event_type: str
    entity_type: str
    entity_id: str
    entity_title: str | None
    entity_url: str | None
    created_at: int


ACTIVITY_YEAR_DAYS = 365


def activity_profile_visible(actor_id: str, entity_owner_id: str | None) -> bool:
    return bool(entity_owner_id and actor_id == entity_owner_id)


def add_user_activity_transact(transacts: list, actor: User, event_type: str, entity_type: str,
                               entity_id: str, entity_title: str | None, entity_url: str | None,
                               entity_owner_id: str | None, now: int | None = None) -> None:
    now = now or utc_now()
    activity_id = str(uuid.uuid4())
    item = {"id": activity_id, "actor_user_id": actor.id, "entity_owner_id": entity_owner_id,
            "profile_visible": activity_profile_visible(actor.id, entity_owner_id),
            "event_type": event_type, "entity_type": entity_type, "entity_id": entity_id,
            "entity_title": entity_title, "entity_url": entity_url, "created_at": now}
    add_dynamodb_put_transact(transacts, (f"USER_ACTIVITY#{actor.id}", f"ACTIVITY#{now}#{activity_id}"), item)


def user_activity_from_dynamodb(item: dict[str, Any]) -> UserActivity:
    entity_type = item["entity_type"]
    entity_id = item["entity_id"]
    entity_url = item.get("entity_url")
    if not entity_url:
        if entity_type == "article":
            entity_url = f"/articles/{entity_id}"
        elif entity_type == "article_tag":
            entity_url = f"/article-tags/{entity_id}"
        elif entity_type == "user":
            entity_url = f"/users/{entity_id}"
    return UserActivity(id=item["id"], event_type=item["event_type"], entity_type=entity_type,
                        entity_id=entity_id, entity_title=item.get("entity_title"),
                        entity_url=entity_url, created_at=int(item["created_at"]))


@dataclass(slots=True)
class UserImpression:
    owner_id: str
    action: UserImpressionAction
    user_id: str
    target_user_id: str
    created_at: int
    updated_at: int | None


@dataclass(slots=True)
class ContactMessage:
    id: str
    name: str
    email: str
    message: str
    user_id: str | None
    created_at: int


def sanitize_forbidden_html(value):
    if not value or not isinstance(value, str):
        return value

    import nh3
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
        },
        attributes={
            "h2": {"id"},
            "h3": {"id"},
            "h4": {"id"},
            "h5": {"id"},
            "h6": {"id"},
            "a": {"href", "title", "target"},
            "img": {"src", "alt"},
            "span": {"class"},
            "div": {"class"},
            "table": {"class", "border", "cellpadding", "cellspacing"},
            "th": {"colspan", "rowspan"},
            "td": {"colspan", "rowspan"},
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

    # Remove image-only paragraph wrappers produced by rich-text editors.
    normalized = re.sub(
        r"<p\b[^>]*>\s*(<img\b[^>]*>)\s*</p>",
        r"\1",
        normalized,
        flags=re.IGNORECASE,
    )

    return normalized


@dataclass(slots=True)
class ArticleTag:
    name: str
    slug: str
    rating: int
    articles_count: int
    image_filename: str | None
    offset: str | None


@dataclass(slots=True)
class ArticleImpression:
    owner_id: str
    article_id: str
    action: ArticleImpressionAction
    user_id: str
    created_at: int
    updated_at: int | None


@dataclass(slots=True)
class Article:
    id: str
    owner_id: str
    title: str
    slug: str
    user_id: str
    user_slug: str | None
    user_name: str | None

    def get_user(self) -> User | None:
        if self.user_name:
            return user_from_dynamodb({
                "id": self.user_id,
                "name": self.user_name,
                "username": self.user_slug,
                "created_at": 0,
            })
        return None

    content: str
    preview: str | None
    tags: list[str]
    status: ArticleStatus
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
class ArticlePublishedEvent:
    article: Article


@dataclass(slots=True)
class ArticleComment:
    id: str
    owner_id: str

    user_id: str
    user_name: str | None
    user_avatar_filename: str | None
    # todo: rename to user_slug
    user_username: str | None

    def get_user(self) -> User:
        return user_from_dynamodb({
            "id": self.user_id,
            "name": self.user_name,
            "avatar_filename": self.user_avatar_filename,
            "username": self.user_username,
            "created_at": 0,
        })

    article_id: str
    article_title: str
    article_slug: str

    def get_article(self) -> Article:
        return article_from_dynamodb({
            "id": self.article_id,
            "user_id": self.user_id,
            "content": "",
            "title": self.article_title,
            "post_slug": self.article_slug,
            "status": ArticleStatus.PUBLISHED,
            "rating_sk": 0,
            "created_at": 0,
        })

    text: str
    rating: int
    likes_count: int
    dislikes_count: int
    replies_count: int
    created_at: int
    updated_at: int | None
    offset: str | None


@dataclass(slots=True)
class ArticleCommentImpression:
    owner_id: str
    article_id: str
    action: ArticleCommentImpressionAction
    user_id: str
    created_at: int
    updated_at: int | None


class Permission(StrEnum):
    REGULAR = "regular"
    ROOT = "root"
    ALL = "*"

    UPDATE_USER = "update_user"
    UPDATE_USER_STATUS = "update_user_status"
    UPDATE_USER_IMPRESSION = "update_user_impression"
    READ_NON_ACTIVE_USER = "read_non_active_user"

    CREATE_ARTICLE = "create_post"
    UPDATE_ARTICLE = "update_post"
    UPDATE_ARTICLE_STATUS = "update_post_status"
    CREATE_CONTACT_MESSAGE = "create_contact_message"
    UPDATE_ARTICLE_IMPRESSION = "toggle_post_impression"
    READ_NON_PUBLISHED_ARTICLE = "read_non_published_post"

    READ_ARTICLE_TAG = "read_post_tag"
    UPDATE_ARTICLE_TAG = "update_post_tag"

    CREATE_ARTICLE_COMMENT = "create_post_comment"
    UPDATE_ARTICLE_COMMENT = "update_post_comment"
    READ_NON_PUBLISHED_ARTICLE_COMMENT = "read_non_published_post_comment"

    UTILS = "utils"
    GENERATE_SITEMAP = "generate_sitemap"
    DROP_CDN_CACHE = "drop_cdn_cache"


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


class ArticleNotFoundError(BaseError):
    pass


class ArticleAlreadyPublishedError(BaseError):
    def __init__(self, message: str = "Article already published", field: str = "title"):
        super().__init__(message=message, field=field)


class ArticleByOldSlugRequestedError(Exception):
    def __init__(self, slug: str, article: Article):
        self.slug = slug
        self.article = article


class ArticleTagNotFoundError(BaseError):
    pass


class ArticleTagByOldSlugRequestedError(Exception):
    def __init__(self, slug: str, article_tag: ArticleTag):
        self.slug = slug
        self.article_tag = article_tag


class ArticleCommentNotFoundError(BaseError):
    pass


class ArticleCommentNonEditableError(BaseError):
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
        "web_base_url": os.getenv("WEB_BASE_URL"),
        "api_base_url": os.getenv("API_BASE_URL"),
        "aws_region": os.getenv("AWS_REGION"),
        "dynamodb_endpoint": os.getenv("DYNAMODB_ENDPOINT"),
        "dynamodb_table": os.getenv("DYNAMODB_TABLE"),
        "google_analytics_id": os.getenv("GOOGLE_ANALYTICS_ID"),
        "tinymce_api_key": os.getenv("TINYMCE_API_KEY"),
        "contact_topic_arn": os.getenv("CONTACT_TOPIC_ARN"),
        "ses_from_email": os.getenv("SES_FROM_EMAIL"),
        "allowed_origin": os.getenv("ALLOWED_ORIGIN"),
        "cognito_domain": os.getenv("COGNITO_DOMAIN"),
        "cognito_client_id": os.getenv("COGNITO_CLIENT_ID"),
        "cognito_client_secret": os.getenv("COGNITO_CLIENT_SECRET"),
        "cognito_user_pool_id": os.getenv("COGNITO_USER_POOL_ID"),
        "static_s3_bucket": os.getenv("STATIC_S3_BUCKET"),
        "email_files_dir": os.getenv("EMAIL_FILES_DIR", "/app-emails"),
        "static_files_dir": os.getenv("STATIC_FILES_DIR", "/app-static"),
        "css_cache_counter": os.getenv("CSS_CACHE_COUNTER", 0),
        "js_cache_counter": os.getenv("JS_CACHE_COUNTER", 0),
        "auth_token_max_age": os.getenv("AUTH_TOKEN_MAX_AGE", 86_400 * 7),
        "auth_jwt_secret": os.getenv("AUTH_JWT_SECRET"),
        "cf_distribution_id": os.getenv("CLOUDFRONT_DISTRIBUTION_ID"),
        "permission_hierarchy": {
            Permission.REGULAR: [
                Permission.UPDATE_USER_IMPRESSION,
                Permission.CREATE_ARTICLE,
                Permission.UPDATE_ARTICLE_IMPRESSION,
                Permission.CREATE_ARTICLE_COMMENT,
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
        **json.load(open("./data.default.json")),
        **json.load(open("./data.json"))
    }


config = get_live_config()


def is_prod():
    return config.get("app_stage") == "prod"


def get_config():
    return config


def get_static_files_dir() -> str:
    return config.get("static_files_dir") or ""


def get_web_base_url() -> str:
    return get_config().get("web_base_url") or ""


def get_api_base_url() -> str:
    return get_config().get("api_base_url") or ""


def get_aws_region():
    return get_config().get("aws_region")


def get_dynamodb_endpoint():
    return get_config().get("dynamodb_endpoint")


def get_dynamodb_table_name():
    return get_config().get("dynamodb_table")


def article_tag_subscription_key(tags: list[str]) -> str:
    return "#".join(sorted(set(sanitize_tags(tags))))


def article_tag_subscription_from_dynamodb(item: dict[str, Any]) -> ArticleTagSubscription:
    return ArticleTagSubscription(item["article_tag_subscription_id"], item["user_id"], item["tags"],
                                  item["created_at"])


def get_user_article_tag_subscriptions(user: User) -> list[ArticleTagSubscription]:
    if user.article_tag_subscriptions_count == 0:
        return []
    response = query_dynamodb_table(
        key_condition_expr=Key("pk").eq(f"USER#{user.id}") & Key("sk").begins_with("ARTICLE_TAG_SUBSCRIPTION#"))
    return [article_tag_subscription_from_dynamodb(item) for item in response.get("Items", [])]


def get_user_article_tag_subscription_for_tags(user: User, tags: list[str]) -> ArticleTagSubscription | None:
    wanted = article_tag_subscription_key(tags)
    return next((item for item in get_user_article_tag_subscriptions(user) if item.key == wanted), None)


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
        resource: object = None,
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
        resource: object = None,
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


def sanitize_tags(value):
    if not value:
        return []
    normalized = [to_kebab_case(tag) for tag in value]
    return list(dict.fromkeys(normalized))


def utc_now() -> int:
    return int(time.time() * 1000)


def dynamodb_transact_write(transacts: list[dict[str, Any]]):
    """
    Executes a DynamoDB TransactWriteItems call and raises a
    DynamoTransactionError with detailed reasons if it fails.
    """
    try:
        get_dynamodb_table().meta.client.transact_write_items(TransactItems=transacts)
    except Exception as e:
        if not is_aws_client_error(e):
            raise
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
    req = ctx.get("request")
    if not req:
        raise ValueError("Request not found in context")
    return get_url(req, name, **params)


@pass_context
def jinja2_user_url(ctx, user: User, **params) -> str:
    return get_user_url(ctx.get("request"), user, **params)


def get_user_url(req, user: User, **params) -> str:
    if user.username:
        return get_url(req, "user-by-slug", slug=user.username, **params)
    return get_static_user_url(req, user, **params)


def get_static_user_url(req, user: User, **params) -> str:
    return get_url(req, "user", user_id=user.id, **params)


@pass_context
def jinja2_article_url(ctx, article: Article, **params) -> str:
    return get_article_url(ctx.get("request"), article, **params)


def get_article_url(req, article: Article, **params) -> str:
    if article.user_slug:
        return get_url(req, "article-by-slugs", user_slug=article.user_slug, article_slug=article.slug, **params)
    return get_static_article_url(req, article, **params)


def get_static_article_url(req, article: Article, **params) -> str:
    return get_url(req, "article", article_id=article.id, **params)


def get_article_comment_url(req, article: Article, article_comment: ArticleComment, **params) -> str:
    return get_article_url(req, article, **params)


def get_current_url(req) -> str:
    path = req.url.path
    query = req.url.query
    return f"{path}?{query}" if query else path


@pass_context
def jinja2_articles_url(ctx, query: ArticleQueryDTO | None = None, **params) -> str:
    return get_articles_url(ctx.get("request"), query=query, **params)


@pass_context
def jinja2_articles_tag_url(ctx, article_tag: ArticleTag, **params) -> str:
    return get_article_tag_url(ctx.get("request"), article_tag, **params)


def get_articles_url(req, query: ArticleQueryDTO | None = None, **params) -> str:
    if not query:
        query = ArticleQueryDTO()

    params = query.get_dict(params)

    slugs: list[str] = []

    type_ = params.pop("type", None)
    if type_:
        type_ = str(type_)
        if type_ != str(ArticleQueryDTO.DEFAULT_TYPE):
            slugs.append(type_)

    tags = params.pop("tags", None)
    if tags:
        slugs.extend(str(t) for t in tags if t)

    status = params.pop("status", None)
    if status:
        status = str(status)
        if status != str(ArticleQueryDTO.DEFAULT_STATUS):
            params["status"] = status

    offset = params.pop("offset", None)
    if offset and offset != ArticleQueryDTO.DEFAULT_OFFSET:
        params["offset"] = offset

    limit = params.pop("limit", None)
    if limit and limit != ArticleQueryDTO.DEFAULT_LIMIT:
        params["limit"] = limit

    if not slugs:
        return get_url(req, "articles", **params)

    return get_url(req, "articles-by-slugs", slugs_path="/".join(slugs), **params)


def get_article_tag_url(req, article_tag: ArticleTag) -> str:
    return get_articles_url(req, tags=[article_tag.slug])


def parse_articles_url_slugs_path(slugs_path: str) -> dict:
    data = {}
    slugs = [p for p in slugs_path.split("/") if p]

    if not slugs:
        return {}

    try:
        data["type"] = ArticleQueryType(slugs[0])
        slugs = slugs[1:]
    except ValueError:
        pass

    data["tags"] = slugs

    return data


@pass_context
def jinja2_users_url(ctx, query: UserQueryDTO | None = None, **params) -> str:
    return get_users_url(ctx.get("request"), query=query, **params)


def get_users_url(req, query: UserQueryDTO | None = None, **params) -> str:
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
        return get_url(req, "users", **params)

    return get_url(req, "users-by-slugs", type=slugs[0], **params)


def get_url(req, name: str, absolute: bool = False, **params) -> str:
    """
    Generate a URL for a named route.
    By default, returns path-only URLs; set absolute=True to prepend the configured route base URL.
    """
    # Find an executable route first, then URL-only metadata routes used by
    # the web Lambda for API links. Metadata routes never handle requests.
    route = next((r for r in req.app.routes if getattr(r, "name", None) == name), None)
    is_metadata_route = route is None
    if route is None:
        route = next((r for r in getattr(req.app, "url_routes", [])
                      if getattr(r, "name", None) == name), None)
    if route is None:
        raise LookupError(f"Unknown route: {name}")
    path_param_names = getattr(route, "param_convertors", {}).keys()

    # Split params into path vs query, skipping None
    path_params = {k: v for k, v in params.items() if k in path_param_names and v is not None}
    query_params = {k: v for k, v in params.items() if k not in path_param_names and v is not None}

    # Use the application router for executable routes and the metadata
    # route itself for URL-only routes.
    url_path_value = (route.url_path_for(name, **path_params) if is_metadata_route
                      else req.url_for(name, **path_params))
    url_path = getattr(url_path_value, "path", str(url_path_value))

    if absolute and url_path == "/":
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

    if absolute:
        base_url = get_api_base_url() if name in API_URL_ROUTES else get_web_base_url()
        return f"{base_url.rstrip("/")}{url_path}"

    return url_path


def get_static_url(req, filename, **params) -> str:
    return get_url(req, "user-by-slug", slug=filename, **params)


@pass_context
def jinja2_static_url(ctx, filename, **params) -> str:
    return get_static_url(ctx.get("request"), filename, **params)


def jinja2_build_responsive_classes(
        sizes: int | dict[str, int],
        prefixes: dict[str, str],
        inverse: bool = False
) -> str:
    """
    Generic helper for responsive Bootstrap-like class builders.

    Converts:
        {"def": 3, "sm": 2} → "col-3 col-sm-2"

    If inverse=True:
        value is transformed as: 12 - value
    """

    if isinstance(sizes, int):
        sizes = {"def": sizes}

    if not isinstance(sizes, dict):
        raise TypeError("Expected dict or int")

    transform = (lambda v: 12 - v) if inverse else None

    classes: list[str] = []

    for k, v in sizes.items():
        if not isinstance(v, int):
            continue

        prefix = prefixes.get(k)
        if prefix is None:
            continue

        final_value = transform(v) if transform else v
        classes.append(f"{prefix}{final_value}")

    return " ".join(classes)


def jinja2_column_classes(sizes, inverse: bool = False) -> str:
    prefixes = {
        "def": "col-",
        "sm": "col-sm-",
        "md": "col-md-",
        "lg": "col-lg-",
        "xl": "col-xl-",
    }

    return jinja2_build_responsive_classes(sizes, prefixes, inverse)


def jinja2_order_classes(orders, inverse: bool = False) -> str:
    prefixes = {
        "def": "order-",
        "sm": "order-sm-",
        "md": "order-md-",
        "lg": "order-lg-",
        "xl": "order-xl-",
    }

    return jinja2_build_responsive_classes(orders, prefixes, inverse)


@pass_context
def jinja2_render_article_content(ctx, content):
    import re

    image_template = jinja2_env().get_template("fragments/image.html")
    context = ctx.get_all()
    image_pattern = re.compile(r"<img\b(?P<attributes>[^>]*)>", re.IGNORECASE)
    attribute_pattern = re.compile(r"([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*([\"'])(.*?)\2")

    def replace_image(match):
        attributes = {
            name.lower(): html.unescape(value)
            for name, _, value in attribute_pattern.findall(match.group("attributes"))
        }
        src = attributes.get("src")
        if not src:
            return ""
        return image_template.render({
            **context,
            "filename": src.rsplit("/", 1)[-1],
            "alt": attributes.get("alt", ""),
            "figure": True,
            "img_sizes": {"1200px": "760px", "992px": "480px", "default": "100vw"},
        })

    return image_pattern.sub(replace_image, content or "")


def get_jinja2_env():
    shared_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    function_templates_dir = os.getenv("FUNCTION_TEMPLATES_DIR", "")
    templates_dirs = [path for path in function_templates_dir.split(os.pathsep) if path]
    templates_dirs.append(shared_templates_dir)
    jinja2_env = Environment(
        loader=FileSystemLoader(templates_dirs),
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=not is_prod(),
        autoescape=select_autoescape(("html", "htm", "xml"))
    )
    jinja2_env.filters.update({
        "unix_to_month_year": unix_to_month_year,
        "unix_to_full_date": unix_to_full_date,
        "iso_utc": jinja2_iso_utc,
        "column_classes": jinja2_column_classes,
        "order_classes": jinja2_order_classes,
    })
    jinja2_env.globals.update(get_config())
    jinja2_env.globals.update({
        "static_url": jinja2_static_url,
        "render_article_content": jinja2_render_article_content,
        "url": jinja2_url,
        "user_url": jinja2_user_url,
        "article_url": jinja2_article_url,
        "articles_url": jinja2_articles_url,
        "users_url": jinja2_users_url,
        "article_tag_url": jinja2_articles_tag_url,
        "Permission": Permission,
        "check_auth": check_authorization,
        "ArticleStatus": ArticleStatus,
        "ArticleImpressionAction": ArticleImpressionAction,
        "UserImpressionAction": UserImpressionAction,
        "ArticleQueryType": ArticleQueryType,
        "UserQueryType": UserQueryType,
        "UserStatus": UserStatus,
        "ArticleQueryDTO": ArticleQueryDTO,
        "UserQueryDTO": UserQueryDTO,
        "article_sentiment_rating": article_sentiment_rating,
        "html_to_text": html_to_text,
        "img_dims": extract_image_filename_dimensions,
    })
    return jinja2_env


jinja2_env = Lazy(get_jinja2_env)


@lru_cache
def get_dynamodb_resource():
    import boto3
    args = {} if is_prod() else {
        "region_name": get_aws_region(),
        "endpoint_url": get_dynamodb_endpoint(),
    }
    return boto3.resource("dynamodb", **args)


@lru_cache
def get_dynamodb_table():
    return get_dynamodb_resource().Table(get_dynamodb_table_name())


def get_html_content(template: str, data: dict[str, Any]) -> str:
    if data is None:
        data = {}
    template = jinja2_env().get_template(template)
    return template.render(data)


def to_datetime(ts: Any) -> datetime:
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
        provider_user_item = get_dynamodb_item(f"PROVIDER_USER#{iss}#{token.sub}", "META")
        if provider_user_item:
            user_id = provider_user_item["user_id"]

            # Fetch user record
            user_item = get_dynamodb_item(f"USER#{user_id}", "META")

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
        add_dynamodb_user_update_transact(transacts, user, {"providers": providers})
    else:
        name = build_user_name(token.name, now)
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
        username = build_user_username(token.name, token.username, now)
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
    return UserTokenDTO(
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

    from jose import jwt
    from jose.exceptions import JWTError, ExpiredSignatureError

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


def article_from_dynamodb(d_item: dict[str, Any]) -> Article:
    owner_id = d_item["user_id"]
    content = d_item["content"]
    return Article(
        id=d_item["id"],
        owner_id=owner_id,
        title=d_item["title"],
        slug=d_item["post_slug"],
        user_id=owner_id,
        user_slug=d_item.get("user_slug"),
        user_name=d_item.get("user_name"),
        content=content,
        preview=d_item.get("preview"),
        tags=d_item.get("tags", []),
        status=d_item["status"],
        comment=d_item.get("comment"),
        rating=d_item["rating_sk"],
        likes_count=d_item.get("likes_count", 0),
        dislikes_count=d_item.get("dislikes_count", 0),
        image_filename=d_item.get("image_filename"),
        redirect_to=d_item.get("redirect_to"),
        comments_count=d_item.get("comments_count", 0),
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at"),
        published_at=d_item.get("published_at"),
        is_premium=False,
        offset=None,
    )


def article_comment_from_dynamodb(d_item: dict[str, Any]) -> ArticleComment:
    owner_id = d_item["user_id"]
    return ArticleComment(
        id=d_item["id"],
        owner_id=owner_id,
        user_id=owner_id,
        user_name=d_item.get("user_name"),
        user_avatar_filename=d_item.get("user_avatar_filename"),
        user_username=d_item.get("user_username"),
        article_id=d_item["post_id"],
        article_title=d_item["post_title"],
        article_slug=d_item.get("comment_post_slug") or d_item["post_slug"],
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


def article_sentiment_rating(likes_count: int, dislikes_count: int) -> dict[str, Any] | None:
    """Return a five-star visual rating derived from binary article feedback."""
    likes_count = max(0, int(likes_count or 0))
    dislikes_count = max(0, int(dislikes_count or 0))
    total_count = likes_count + dislikes_count
    if not total_count:
        return None

    score = likes_count / total_count * 5
    rounded_half_score = math.floor(score * 2 + 0.5) / 2
    filled_stars = math.floor(rounded_half_score)
    return {
        "score": round(score, 1),
        "total_count": total_count,
        "filled_stars": filled_stars,
        "has_half_star": rounded_half_score - filled_stars == 0.5,
    }


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", value,
                   flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def get_text_diff_percentage(t1, t2) -> int:
    import difflib
    seq = difflib.SequenceMatcher(None, t1, t2)
    similarity = seq.ratio()
    change_percentage = (1 - similarity) * 100
    return int(change_percentage)


def add_put_article_tag_combos_transact(transacts: list, article: Article, slug: str | None = None) -> None:
    from itertools import combinations
    for r in range(1, len(article.tags) + 1):
        for combo in combinations(sorted(article.tags), r):
            if slug is None or slug in combo:
                article_tag_combo_key = ("POST_TAG_COMBO#" + "#".join(combo),
                                         f"POST#{article.created_at}#{article.id}")
                add_dynamodb_put_transact(transacts, article_tag_combo_key, {"post_id": article.id})


def add_delete_article_tag_combos_transact(transacts: list, article: Article, slug: str | None = None) -> None:
    from itertools import combinations
    for r in range(1, len(article.tags) + 1):
        for combo in combinations(sorted(article.tags), r):
            if slug is None or slug in combo:
                article_tag_combo_key = ("POST_TAG_COMBO#" + "#".join(combo),
                                         f"POST#{article.created_at}#{article.id}")
                add_dynamodb_delete_transact(transacts, article_tag_combo_key)


def add_increase_article_tags_rating_transact(transacts: list, tags: list, now):
    for tag in tags:
        transacts.append({
            "Update": {
                "TableName": get_dynamodb_table_name(),
                "Key": {
                    "pk": f"POST_TAG#{tag}",
                    "sk": "META"
                },
                "UpdateExpression": (
                    "SET #new_tag_name_sk = if_not_exists(#new_tag_name_sk, :tag_name_sk), "
                    "    #new_name = if_not_exists(#new_name, :name), "
                    "    #new_tag_type_pk = if_not_exists(#new_tag_type_pk, :tag_type_pk), "
                    "    #new_rating_sk = if_not_exists(#new_rating_sk, :def_rating_sk) + :rating_sk_inc, "
                    "    #posts_count = if_not_exists(#posts_count, :zero) + :inc, "
                    "    #new_created_at = if_not_exists(#new_created_at, :now), "
                    "    #new_updated_at = :now "
                ),
                "ExpressionAttributeNames": {
                    "#new_tag_name_sk": "tag_name_sk",
                    "#new_name": "name",
                    "#new_tag_type_pk": "tag_type_pk",
                    "#new_rating_sk": "rating_sk",
                    "#posts_count": "posts_count",
                    "#new_created_at": "created_at",
                    "#new_updated_at": "updated_at",
                },
                "ExpressionAttributeValues": {
                    ":tag_name_sk": tag,
                    ":name": tag,
                    ":tag_type_pk": "POST_TAG",
                    ":now": now,
                    ":def_rating_sk": compute_rating_sk(0, now),
                    ":rating_sk_inc": compute_rating_sk(1),
                    ":zero": 0,
                    ":inc": 1,
                }
            }
        })


def add_decrease_article_tags_rating_transact(transacts: list, tags: list, now):
    for tag in tags:
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


def find_article(article_id: str) -> Article | None:
    item = get_dynamodb_item(f"POST#{article_id}", "META")
    return article_from_dynamodb(item) if item else None


def get_article(article_id: str, cur_user: User = None) -> Article:
    article = find_article(article_id)
    if article is None:
        raise ArticleNotFoundError(f"Post '{article_id}' not found")
    if article.status != ArticleStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_ARTICLE, article)
    return article


def find_article_slug_item(slug: str) -> dict[str, Any] | None:
    resp = query_dynamodb_table(
        index_name="POSTS_BY_SLUG",
        key_condition_expr=Key("post_slug").eq(slug),
    )
    for item in resp.get("Items", []):
        if item.get("sk") == "META":
            return item
    return None


def find_article_by_slug(slug: str) -> Article | None:
    item = find_article_slug_item(slug)
    # logger.debug(f"Post by slug: {item}")
    return article_from_dynamodb(item) if item else None


def find_article_by_slug_follow_redirects(slug: str) -> Article | None:
    visited = set()
    current_slug = slug

    while True:
        if current_slug in visited:
            raise RuntimeError("Redirect loop detected")

        visited.add(current_slug)

        item = find_article_slug_item(current_slug)
        if not item:
            return None

        redirect_to = item.get("redirect_to")
        if redirect_to:
            current_slug = redirect_to
            continue

        return article_from_dynamodb(item)


def get_article_by_slugs(user_slug: str, article_slug: str, cur_user: User = None) -> Article:
    article = find_article_by_slug_follow_redirects(article_slug)
    if article is None:
        raise ArticleNotFoundError(f"Post '{article_slug}' not found")
    if article.user_slug != user_slug:
        raise UserNotFoundError(f"User '{user_slug}' not found")
    if article.status != ArticleStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_ARTICLE, article)
    if article.slug != article_slug:
        raise ArticleByOldSlugRequestedError(article_slug, article)
    return article


def find_article_comment(article_id: str, article_comment_id: str) -> ArticleComment | None:
    item = get_dynamodb_item(f"POST#{article_id}", f"COMMENT#{article_comment_id}")
    return article_comment_from_dynamodb(item) if item else None


def get_article_comment(article_id: str, article_comment_id: str) -> ArticleComment:
    article_comment = find_article_comment(article_id, article_comment_id)
    if article_comment is None:
        raise ArticleCommentNotFoundError(f"Article comment '{article_comment_id}' not found")
    return article_comment


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
        published_articles_count=d_item.get("published_posts_count", 0),
        unpublished_articles_count=d_item.get("unpublished_posts_count", 0),
        rejected_articles_count=d_item.get("rejected_posts_count", 0),
        rating=d_item.get("rating_sk", 0),
        followers_count=d_item.get("followers_count", 0),
        following_count=d_item.get("following_count", 0),
        comment=d_item.get("comment"),
        article_comments_count=d_item.get("post_comments_count", 0),
        article_tag_subscriptions_count=d_item.get("article_tag_subscriptions_count", 0),
        bmc_username=d_item.get("bmc_username"),
        redirect_to=d_item.get("redirect_to"),
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at"),
        offset=None,
        show_activity_calendar=d_item.get("show_activity_calendar", False),
        show_recent_activity=d_item.get("show_recent_activity", False),
        show_interests=d_item.get("show_interests", True),
    )


def find_user(user_id: str) -> User | None:
    item = get_dynamodb_item(f"USER#{user_id}", "META")
    return user_from_dynamodb(item) if item else None


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
    item = get_dynamodb_item(f"USER#{cur_user.id}", f"REL#{user.id}")
    return user_impression_from_dynamodb(item) if item else None


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
    if not changes and not deltas:
        return
    param_dict = dict(locals())
    param_dict.pop("transacts", None)
    transacts.append(build_dynamodb_update_item_params(**param_dict))


def add_dynamodb_obj_update_transact(transacts: list, obj: object, key: tuple[str, str],
                                     changes: dict[str, Any] | None = None,
                                     deltas: dict[str, Any] | None = None) -> None:
    add_dynamodb_update_transact(transacts, key, changes=changes, deltas=deltas)
    if changes:
        for k, v in changes.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
    if deltas:
        for k, delta in deltas.items():
            if hasattr(obj, k):
                setattr(obj, k, getattr(obj, k) + delta)


def add_dynamodb_user_update_transact(transacts: list, user: User, changes: dict[str, Any] | None = None,
                                      deltas: dict[str, Any] | None = None) -> None:
    return add_dynamodb_obj_update_transact(transacts, user, (f"USER#{user.id}", "META"), changes=changes,
                                            deltas=deltas)


def add_dynamodb_article_update_transact(transacts: list, article: Article, changes: dict[str, Any] | None = None,
                                         deltas: dict[str, Any] | None = None) -> None:
    return add_dynamodb_obj_update_transact(transacts, article, (f"POST#{article.id}", "META"), changes=changes,
                                            deltas=deltas)


def add_dynamodb_article_tag_update_transact(transacts: list, article_tag: ArticleTag,
                                             changes: dict[str, Any] | None = None,
                                             deltas: dict[str, Any] | None = None) -> None:
    return add_dynamodb_obj_update_transact(transacts, article_tag, (f"POST_TAG#{article_tag.slug}", "META"),
                                            changes=changes,
                                            deltas=deltas)


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


def get_dynamodb_item(pk: str, sk: str) -> dict[str, Any] | None:
    resp = get_dynamodb_table().get_item(Key={"pk": pk, "sk": sk})
    return resp.get("Item")


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
        if isinstance(o, Decimal):
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


def get_articles(query_dto: ArticleQueryDTO = None, cur_user: User = None) -> list[Article]:
    if query_dto is None:
        query_dto = ArticleQueryDTO()
    if query_dto.type == ArticleQueryType.POPULAR:
        if query_dto.tags:
            return get_popular_articles_by_tags(query_dto, cur_user)
        return get_popular_articles(query_dto, cur_user)
    if query_dto.tags:
        return get_latest_articles_by_tags(query_dto, cur_user)
    return get_latest_articles(query_dto, cur_user)


def query_dynamodb_table(
        index_name: str | None = None,
        key_condition_expr: Any = None,
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
    except Exception as e:
        if not is_aws_client_error(e):
            raise
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
        key_condition_expr: Any = None,
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

    # DynamoDB can return fewer items than Limit when the response reaches its
    # 1 MB page-size cap, so paginate based on LastEvaluatedKey rather than the
    # number of returned items.
    if results and resp.get("LastEvaluatedKey"):
        results[-1].offset = encode_offset(resp["LastEvaluatedKey"])

    return results


def get_latest_articles(query_dto: ArticleQueryDTO = None, cur_user: User = None) -> list[Article]:
    if query_dto is None:
        query_dto = ArticleQueryDTO()

    if query_dto.status != ArticleStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_ARTICLE)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_STATUS_CREATED_AT_2",
        key_condition_expr=Key("post_status_pk").eq(f"POST#{query_dto.status}"),
        map_fn=article_from_dynamodb,
    )


def get_popular_articles(query_dto: ArticleQueryDTO = None, cur_user: User = None) -> list[Article]:
    if query_dto is None:
        query_dto = ArticleQueryDTO()

    if query_dto.status != ArticleStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_ARTICLE)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_STATUS_RATING",
        key_condition_expr=Key("post_status_pk").eq(f"POST#{query_dto.status}"),
        map_fn=article_from_dynamodb,
    )


def get_latest_articles_by_tags(query_dto: ArticleQueryDTO = None, cur_user: User = None) -> list[Article]:
    if query_dto is None:
        query_dto = ArticleQueryDTO()
    if not query_dto.tags:
        return get_latest_articles(query_dto, cur_user)

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
    article_ids = set([item["post_id"] for item in combo_items])
    keys = [{"pk": f"POST#{article_id}", "sk": "META"} for article_id in article_ids]
    resp = table.meta.client.batch_get_item(RequestItems={table.name: {"Keys": keys}})
    article_items = resp["Responses"].get(table.name, [])

    # Maintain original order
    article_items_map = {item["id"]: item for item in article_items}
    ordered_articles = [article_items_map[pid] for pid in article_ids if pid in article_items_map]

    articles = [article_from_dynamodb(item) for item in ordered_articles]
    if len(articles) == query_dto.limit:
        articles[-1].offset = encode_offset(resp.get("LastEvaluatedKey"))
    return articles


def get_article_comments(article: Article, query_dto: ArticleCommentQueryDTO | None = None) -> list[ArticleComment]:
    if article.comments_count == 0:
        return []
    if query_dto is None:
        query_dto = ArticleCommentQueryDTO()

    return query_dynamodb_items(
        query_dto=query_dto,
        key_condition_expr=Key("pk").eq(f"POST#{article.id}") & Key('sk').begins_with(f"COMMENT#"),
        map_fn=article_comment_from_dynamodb,
    )


def get_popular_articles_by_tags(
        query_dto: ArticleQueryDTO = None,
        cur_user: User = None,
        or_mode: bool = False
) -> list[Article]:
    if query_dto is None:
        query_dto = ArticleQueryDTO()

    # Increase limit to fetch more posts before filtering
    query_dto_copy = copy.copy(query_dto)
    query_dto_copy.limit = max(query_dto.limit * 5, 100)

    articles = get_popular_articles(query_dto_copy, cur_user)

    if not query_dto.tags:
        return articles

    offset = articles[-1].offset if articles else None

    # Filter by tags
    wanted_tags = set(query_dto.tags)
    if or_mode:
        filtered_articles = [article for article in articles if wanted_tags.intersection(article.tags)]
    else:
        filtered_articles = [article for article in articles if wanted_tags.issubset(set(article.tags))]
    if filtered_articles:
        filtered_articles[-1].offset = offset

    return filtered_articles


def article_tag_from_dynamodb(d_item: dict[str, Any]) -> ArticleTag:
    # logger.debug(d_item)
    slug = d_item["tag_name_sk"]
    return ArticleTag(
        name=d_item.get("name") or slug,
        slug=slug,
        rating=d_item["rating_sk"],
        articles_count=d_item.get("posts_count", 0),
        image_filename=d_item.get("image_filename"),
        offset=None,
    )


def get_popular_article_tags(query_dto: ArticleTagQueryDTO = None) -> list[ArticleTag]:
    if query_dto is None:
        query_dto = ArticleTagQueryDTO()

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="TAGS_BY_TYPE_RATING",
        key_condition_expr=Key("tag_type_pk").eq("POST_TAG"),
        map_fn=article_tag_from_dynamodb,
    )


def find_article_tag_slug_item(slug: str) -> dict[str, Any] | None:
    item = get_dynamodb_item(f"POST_TAG#{slug}", "META")
    if item:
        return item
    return get_dynamodb_item(f"POST_TAG_REDIRECT#{slug}", "META")


def find_article_tag_by_slug_follow_redirects(slug: str) -> ArticleTag | None:
    visited = set()
    current_slug = slug

    while True:
        if current_slug in visited:
            raise RuntimeError("Redirect loop detected")

        visited.add(current_slug)

        item = find_article_tag_slug_item(current_slug)
        if not item:
            return None

        redirect_to = item.get("redirect_to")
        if redirect_to:
            current_slug = redirect_to
            continue

        return article_tag_from_dynamodb(item)


def find_article_tag(slug: str) -> ArticleTag | None:
    return find_article_tag_by_slug_follow_redirects(slug)


def get_article_tag(slug: str, cur_user: User) -> ArticleTag:
    article_tag = find_article_tag_by_slug_follow_redirects(slug)
    if article_tag is None:
        raise ArticleTagNotFoundError(f"Article tag '{slug}' not found")
    verify_authorization(cur_user, Permission.READ_ARTICLE_TAG, article_tag)
    if article_tag.slug != slug:
        raise ArticleTagByOldSlugRequestedError(slug, article_tag)
    return article_tag


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
        query_dto = ArticleQueryDTO()
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


def get_latest_published_articles_by_user(user: User) -> list[Article]:
    return get_latest_articles_by_user(user)


def get_latest_articles_by_user(user: User, query_dto: ArticleQueryDTO = None, cur_user: User = None) -> list[Article]:
    if query_dto is None:
        query_dto = ArticleQueryDTO()

    if query_dto.status != ArticleStatus.PUBLISHED:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_PUBLISHED_ARTICLE, user)

    if getattr(user, f"{query_dto.status}_articles_count") == 0:
        return []

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POSTS_BY_USER_STATUS_CREATED_AT_2",
        key_condition_expr=Key("post_user_status_pk").eq(f"POST#{user.id}#{query_dto.status}"),
        map_fn=article_from_dynamodb,
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


def article_impression_from_dynamodb(d_item: dict[str, Any]) -> ArticleImpression:
    user_id = d_item["user_id"]
    return ArticleImpression(
        owner_id=user_id,
        article_id=d_item["post_id"],
        user_id=user_id,
        action=d_item["action"],
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at")
    )


def find_article_impression(article: Article, user: User) -> ArticleImpression | None:
    item = get_dynamodb_item(f"POST#{article.id}", f"IMP#{user.id}")
    return article_impression_from_dynamodb(item) if item else None


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


def extract_image_filename_dimensions(filename: str) -> tuple[int | None, int | None]:
    if not filename:
        return None, None

    match = re.search(r'_(\d+)x(\d+)(?:\.\w+)?$', filename)
    if match:
        return match.group(1), match.group(2)

    return None, None
