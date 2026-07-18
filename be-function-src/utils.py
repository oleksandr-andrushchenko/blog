import time
import re
import os
import uuid
import datetime
import logging
import sys
from enum import StrEnum
from urllib.parse import quote, urlencode, urlparse
import base64
from typing import Callable, ClassVar, Literal, TypeVar, Any, Optional
from jinja2 import Environment, FileSystemLoader, pass_context
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import copy
from functools import lru_cache, partial
import asyncio
from dataclasses import dataclass, asdict
from decimal import Decimal
from validation import validate_email_address, validate_http_url
from query_dtos import (BaseQueryDTO, ArticleCommentQueryDTO, ArticleQueryDTO, ArticleQueryType, ArticleStatus, ArticleTagQueryDTO, UserQueryDTO, UserQueryType, UserStatus)
from basic_dtos import ContactMessageDTO, FileDTO, ImageFileDTO, UserTokenDTO
from user_dtos import UpdateUserDTO, UpdateUserImpressionDTO, UpdateUserStatusDTO, UserImpressionAction
from article_dtos import (ArticleCommentDTO, ArticleCommentImpressionAction, ArticleDTO, ArticleImpressionAction, UpdateArticleCommentDTO, UpdateArticleCommentImpressionDTO, UpdateArticleDTO, UpdateArticleImpressionDTO, UpdateArticleStatusDTO, UpdateArticleTagDTO)


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
    bmc_username: str | None
    redirect_to: str | None
    cdn_cache_version: int
    created_at: int
    updated_at: int | None
    offset: str | None


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


def sanitize_html(value):
    if not value or not isinstance(value, str):
        return value

    import html
    escaped = html.escape(value)
    return escaped.strip()


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
            "figure", "figcaption",
        },
        attributes={
            "h2": {"id"},
            "h3": {"id"},
            "h4": {"id"},
            "h5": {"id"},
            "h6": {"id"},
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
class ArticleComment:
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



def update_article_tag(article_tag: ArticleTag, update_article_tag_dto: UpdateArticleTagDTO, cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_TAG, article_tag)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_article_tag_dto.changes()
    if not changes:
        return

    now = utc_now()

    new_name = changes.pop("name", None)
    if new_name is not None:
        new_name = sanitize_html(new_name).strip()
        changes["name"] = new_name

    image_action = changes.pop("image_action", "keep")

    if image_action == "delete":
        changes["image_filename"] = None
    elif image_action == "keep":
        changes.pop("image_filename", None)

    old_image = article_tag.image_filename
    old_slug = article_tag.slug
    new_slug = to_kebab_case(changes["name"]) if "name" in changes else old_slug
    slug_changed = new_slug != old_slug
    transacts = []

    if slug_changed:
        old_item = get_dynamodb_item(f"POST_TAG#{old_slug}", "META")
        if old_item is None:
            raise ArticleTagNotFoundError(f"Article tag '{old_slug}' not found")

        new_item = {k: v for k, v in old_item.items() if k not in {"pk", "sk"}}
        new_item.update(changes)
        new_item["tag_name_sk"] = new_slug
        new_item["updated_at"] = now

        redirect_item = {
            "tag_name_sk": old_slug,
            "redirect_to": new_slug,
            "created_at": now,
        }
        add_dynamodb_put_transact(transacts, (f"POST_TAG_REDIRECT#{old_slug}", "META"), redirect_item, new_pk_only=True)
        add_dynamodb_put_transact(transacts, (f"POST_TAG#{new_slug}", "META"), new_item, new_pk_only=True)
        add_dynamodb_delete_transact(transacts, (f"POST_TAG#{old_slug}", "META"))

        from itertools import combinations
        for article in get_latest_articles_by_tags(ArticleQueryDTO(tags=[old_slug], limit=1000)):
            old_tags = list(article.tags)
            new_tags = list(dict.fromkeys(new_slug if tag == old_slug else tag for tag in old_tags))
            add_dynamodb_article_update_transact(transacts, article, {"tags": new_tags})

            for r in range(1, len(old_tags) + 1):
                for combo in combinations(sorted(old_tags), r):
                    if old_slug in combo:
                        add_dynamodb_delete_transact(
                            transacts,
                            ("POST_TAG_COMBO#" + "#".join(combo), f"POST#{article.created_at}#{article.id}")
                        )

            for r in range(1, len(new_tags) + 1):
                for combo in combinations(sorted(new_tags), r):
                    if new_slug in combo:
                        add_dynamodb_put_transact(
                            transacts,
                            ("POST_TAG_COMBO#" + "#".join(combo), f"POST#{article.created_at}#{article.id}"),
                            {"post_id": article.id}
                        )
    else:
        add_dynamodb_article_tag_update_transact(transacts, article_tag, changes)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="name")
        raise

    if "name" in changes:
        article_tag.name = changes["name"]
    if slug_changed:
        article_tag.slug = new_slug
    if "image_filename" in changes:
        article_tag.image_filename = changes["image_filename"]

    if old_image and image_action in {"delete", "replace"}:
        drop_public_file(old_image)

    # Invalidate CDN cache globally
    _drop_cdn_cache(
        _get_index_url(req),
        _get_article_tag_urls(article_tag, req),
        _get_articles_urls(req) if slug_changed else [],
    )


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


def get_url(req, name: str, full: bool = False, **params) -> str:
    """
    Generate a URL for a named route.
    By default, returns path-only URLs; set full=True to prepend base_url.
    """
    # Find the route
    route = next(r for r in req.app.routes if getattr(r, "name", None) == name)
    path_param_names = getattr(route, "param_convertors", {}).keys()

    # Split params into path vs query, skipping None
    path_params = {k: v for k, v in params.items() if k in path_param_names and v is not None}
    query_params = {k: v for k, v in params.items() if k not in path_param_names and v is not None}

    # Use req.url_for to get the path
    url_path = req.url_for(name, **path_params).path

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

    for key, value in sizes.items():
        if not isinstance(value, int):
            continue

        prefix = prefixes.get(key)
        if prefix is None:
            continue

        final_value = transform(value) if transform else value
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
        "column_classes": jinja2_column_classes,
        "order_classes": jinja2_order_classes,
    })
    jinja2_env.globals.update(get_config())
    jinja2_env.globals.update({
        "static_url": jinja2_static_url,
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


@lru_cache
def get_s3_client():
    import boto3
    return boto3.client("s3")


@lru_cache
def _get_cf_client():
    import boto3
    return boto3.client("cloudfront")


@lru_cache
def get_sns_client():
    import boto3
    return boto3.client("sns")


def drop_cdn_cache(user: User) -> tuple[bool, int]:
    verify_authorization(user, Permission.DROP_CDN_CACHE)
    res = _drop_cdn_cache()
    return res.get("success"), res.get("items_count")


def _drop_cdn_cache(*urls) -> dict[str, Any]:
    items = set()
    for u in urls:
        if isinstance(u, str):
            items.add(u)
        elif isinstance(u, (list, tuple, set)):
            items.update(u)
        else:
            raise TypeError(f"Unsupported type: {type(u)}")

    # Resolve paths
    if items:
        paths = []
        for p in items:
            if not isinstance(p, str):
                raise TypeError(f"Invalid path type: {type(p)} (expected str)")

            if not p.startswith("/"):
                raise ValueError(f"Invalid CloudFront path (must start with '/'): {p}")

            paths.append(p)

        if len(paths) > 3000:
            raise ValueError("CloudFront supports max 3000 paths per invalidation request")
    else:
        paths = ["/*"]

    if True or not is_prod():
        return {
            "success": True,
            "invalidation_id": "",
            "status": "InProgress",
            "items_count": len(paths),
        }

    client = _get_cf_client()
    distribution_id = get_cf_distribution_id()
    response = client.create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "Paths": {
                "Quantity": len(paths),
                "Items": paths,
            },
            "CallerReference": str(uuid.uuid4()),
        },
    )

    metadata = response.get("ResponseMetadata", {})
    invalidation = response.get("Invalidation", {})

    return {
        "success": metadata.get("HTTPStatusCode") == 201,
        "invalidation_id": invalidation.get("Id"),
        "status": invalidation.get("Status"),
        "items_count": len(paths),
    }


def get_html_content(template: str, data: dict[str, Any]) -> str:
    if data is None:
        data = {}
    template = jinja2_env().get_template(template)
    return template.render(data)


def get_image_dimensions(data: bytes) -> tuple[int, int]:
    """Return (width, height) for JPEG, PNG, GIF images from raw bytes."""

    import struct

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
    from io import BytesIO
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


def find_preview(html_content: str) -> str | None:
    from html_parsers import FirstPExtractor
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
        rf'/?'
        rf'([0-9a-fA-F-]+(?:_[0-9]+x[0-9]+)?\.'
        rf'(?:{allowed_extensions}))["\']'
    )

    match = re.search(pattern, html_content, flags=re.IGNORECASE)
    if not match:
        return None

    return match.group(1)


def create_article(article_dto: ArticleDTO, cur_user: User) -> Article:
    verify_authorization(cur_user, Permission.CREATE_ARTICLE)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    now = utc_now()
    status = ArticleStatus.UNPUBLISHED
    article_id = str(uuid.uuid4())
    title = sanitize_html(article_dto.title)
    content = sanitize_forbidden_html(article_dto.content)
    preview = find_preview(content)
    image_filename = find_static_image_filename(content)
    tags = sanitize_tags(article_dto.tags)
    slug = to_kebab_case(title)

    transacts = []

    article_item = {
        "id": article_id,
        "title": title,
        "post_slug": slug,
        "user_id": cur_user.id,
        "content": content,
        "tags": tags,
        "rating_sk": compute_rating_sk(0, now),
        "status": status,
        "created_at": now,
        "post_status_pk": f"POST#{status}",
        "post_user_status_pk": f"POST#{cur_user.id}#{status}",
    }
    if preview:
        article_item["preview"] = preview
    if image_filename:
        article_item["image_filename"] = image_filename
    if cur_user.username:
        article_item["user_slug"] = cur_user.username
    add_dynamodb_put_transact(transacts, (f"POST#{article_id}", "META"), article_item, new_pk_only=True)

    add_dynamodb_user_update_transact(transacts, cur_user, deltas={
        "unpublished_posts_count": 1,
        # Invalidate CDN cache for current user
        "cdn_cache_version": 1,
    })
    # todo: should be unique in combination with username (cur_user, post)
    add_dynamodb_put_transact(transacts, (f"POST_SLUG#{slug}", "META"), {"post_id": article_id}, new_pk_only=True)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    return article_from_dynamodb(article_item)


def get_text_diff_percentage(t1, t2) -> int:
    import difflib
    seq = difflib.SequenceMatcher(None, t1, t2)
    similarity = seq.ratio()
    change_percentage = (1 - similarity) * 100
    return int(change_percentage)


def update_article(article: Article, update_article_dto: UpdateArticleDTO, cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE, article)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_article_dto.changes()
    if not changes:
        return

    old_status = article.status
    published_already = old_status == ArticleStatus.PUBLISHED
    should_set_status_to_unpublished = False
    now = utc_now()

    for k, v in changes.items():
        if k == "title":
            changes[k] = sanitize_html(v)
        elif k == "content":
            changes[k] = sanitize_forbidden_html(v)
        elif k == "tags":
            changes[k] = sanitize_tags(v)

    transacts = []

    old_title = article.title
    title = changes.get("title", old_title)
    if title != old_title:
        if published_already and get_text_diff_percentage(old_title, title) > 10:
            should_set_status_to_unpublished = True
        old_slug = article.slug
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
            add_dynamodb_put_transact(transacts, (f"POST_SLUG#{slug}", "META"), {"post_id": article.id}, new_pk_only=True)

    old_content = article.content
    content = changes.get("content", old_content)
    if content != old_content:
        if published_already and get_text_diff_percentage(old_content, content) > 10:
            should_set_status_to_unpublished = True
        changes["preview"] = find_preview(content)
        changes["image_filename"] = find_static_image_filename(content)

    old_tags = sorted(article.tags)
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
            from itertools import combinations
            for r in range(1, len(old_tags) + 1):
                for combo in combinations(sorted(old_tags), r):
                    article_tag_combo_key = ("POST_TAG_COMBO#" + "#".join(combo), f"POST#{article.created_at}#{article.id}")
                    add_dynamodb_delete_transact(transacts, article_tag_combo_key)

    if published_already and should_set_status_to_unpublished:
        changes["status"] = ArticleStatus.UNPUBLISHED

    article_owner = get_user(article.owner_id)
    # Invalidate CDN cache for post owner
    article_owner_deltas = {"cdn_cache_version": 1}

    status = changes.get("status", article.status)
    status_changed = status != old_status
    if status_changed:
        # Update post lists
        changes["post_status_pk"] = f"POST#{status}"
        changes["post_user_status_pk"] = f"POST#{article.user_id}#{status}"

        # User post counters
        article_owner_deltas[f"{old_status}_posts_count"] = -1
        article_owner_deltas[f"{status}_posts_count"] = 1

    add_dynamodb_user_update_transact(transacts, article_owner, deltas=article_owner_deltas)
    add_dynamodb_article_update_transact(transacts, article, changes)

    if cur_user.id != article_owner.id:
        add_dynamodb_user_update_transact(transacts, cur_user, deltas={
            # Invalidate CDN cache for current user
            "cdn_cache_version": 1
        })

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    for k, v in changes.items():
        if k == "post_slug":
            k = "slug"
        if hasattr(article, k):
            setattr(article, k, v)

    # Invalidate CDN cache globally
    _drop_cdn_cache(
        _get_article_urls(article, req),
        _get_user_urls(article_owner, req),
        _get_index_url(req) if status_changed else [],
        _get_articles_urls(req) if status_changed else [],
    )


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


def create_article_comment(article: Article, article_comment_dto: ArticleCommentDTO, cur_user: User, req) -> ArticleComment:
    verify_authorization(cur_user, Permission.CREATE_ARTICLE_COMMENT)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    now = utc_now()
    comment_id = f"{now}#{str(uuid.uuid4())}"

    transacts = []

    article_comment_item = {
        "id": comment_id,

        "user_id": cur_user.id,
        "user_name": cur_user.name,
        "user_avatar_filename": cur_user.avatar_filename,
        "user_username": cur_user.username,

        "post_id": article.id,
        "post_title": article.title,
        "comment_post_slug": article.slug,
        "post_comment_pk": f"POST_COMMENT",

        "text": article_comment_dto.text,
        "created_at": now,
    }

    add_dynamodb_put_transact(transacts, (f"POST#{article.id}", f"COMMENT#{comment_id}"), article_comment_item)
    add_dynamodb_article_update_transact(transacts, article, deltas={"comments_count": 1})
    add_dynamodb_user_update_transact(transacts, cur_user, deltas={
        "post_comments_count": 1,
        # Invalidate CDN cache for current user
        "cdn_cache_version": 1,
    })

    if cur_user.id != article.owner_id:
        article_owner = get_user(article.owner_id)
        add_dynamodb_user_update_transact(transacts, article_owner, deltas={
            # Invalidate CDN cache for post owner
            "cdn_cache_version": 1
        })

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    # Invalidate CDN cache for post page and index globally
    _drop_cdn_cache(
        _get_article_urls(article, req),
        _get_index_url(req),
    )

    return article_comment_from_dynamodb(article_comment_item)


def update_article_comment(article: Article, article_comment: ArticleComment, update_article_comment_dto: UpdateArticleCommentDTO,
                        cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_COMMENT, article_comment)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    if article_comment.likes_count != 0 or article_comment.dislikes_count != 0:
        raise ArticleCommentNonEditableError()

    changes = update_article_comment_dto.changes()
    if not changes:
        return

    for k, v in changes.items():
        if k == "text":
            changes[k] = sanitize_html(v)

    transacts = []

    add_dynamodb_update_transact(transacts, (f"POST#{article.id}", f"COMMENT#{article_comment.id}"), changes)

    add_dynamodb_user_update_transact(transacts, cur_user, deltas={
        # Invalidate CDN cache for current user
        "cdn_cache_version": 1
    })

    if cur_user.id != article.owner_id:
        article_owner = get_user(article.owner_id)
        add_dynamodb_user_update_transact(transacts, article_owner, deltas={
            # Invalidate CDN cache for post owner
            "cdn_cache_version": 1
        })

    dynamodb_transact_write(transacts)

    for key, value in changes.items():
        if hasattr(article_comment, key):
            setattr(article_comment, key, value)

    # Invalidate CDN cache for post page globally
    _drop_cdn_cache(
        _get_article_urls(article, req),
    )


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
        bmc_username=d_item.get("bmc_username"),
        redirect_to=d_item.get("redirect_to"),
        cdn_cache_version=d_item.get("cdn_cache_version", 0),
        created_at=d_item["created_at"],
        updated_at=d_item.get("updated_at"),
        offset=None,
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


def add_dynamodb_article_tag_update_transact(transacts: list, article_tag: ArticleTag, changes: dict[str, Any] | None = None,
                                          deltas: dict[str, Any] | None = None) -> None:
    return add_dynamodb_obj_update_transact(transacts, article_tag, (f"POST_TAG#{article_tag.slug}", "META"), changes=changes,
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


def update_dynamodb_item(
        key: tuple[str, str],
        changes: dict[str, Any] | None = None,
        deltas: dict[str, Any] | None = None,
        add_updated_at: bool = True
) -> None:
    param_dict = dict(locals())
    update_item_params = build_dynamodb_update_item_params(**param_dict)
    get_dynamodb_table().update_item(**update_item_params["Update"])


def get_dynamodb_item(pk: str, sk: str) -> dict[str, Any] | None:
    resp = get_dynamodb_table().get_item(Key={"pk": pk, "sk": sk})
    return resp.get("Item")


def update_user(user: User, update_user_dto: UpdateUserDTO, cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_user_dto.changes()
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
                articles = get_latest_published_articles_by_user(user)
                for article in articles:
                    add_dynamodb_article_update_transact(transacts, article, {"user_slug": slug})
        else:
            add_dynamodb_delete_transact(transacts, (f"USER_SLUG#{old_slug}", "META"))
            articles = get_latest_published_articles_by_user(user)
            for article in articles:
                add_dynamodb_article_update_transact(transacts, article, {"user_slug": None})

    add_dynamodb_user_update_transact(transacts, user, changes, {
        # Invalidate CDN cache for user
        "cdn_cache_version": 1,
    })

    if user.id != cur_user.id:
        add_dynamodb_user_update_transact(transacts, cur_user, deltas={
            # Invalidate CDN cache for current user
            "cdn_cache_version": 1
        })

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="username")
        raise

    if old_avatar and avatar_action in {"delete", "replace"}:
        drop_public_file(old_avatar)

    # Invalidate CDN cache globally
    _drop_cdn_cache(
        _get_user_urls(user, req),
        # todo: add checks (if only photo,name or headline has changed)
        _get_user_article_urls(user, req),
        # todo: index page (if popular user)
        # todo: users page (if on top)
    )


def update_user_status(user: User, update_user_status_dto: UpdateUserStatusDTO, cur_user: User, req) -> None:
    # logger.debug(f"update_user_status: user: {user}, cur_user: {cur_user}")
    verify_authorization(cur_user, Permission.UPDATE_USER_STATUS)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_user_status_dto.changes()
    if not changes:
        return
    for k, v in changes.items():
        changes[k] = sanitize_html(v)
    if not "comment" in changes:
        changes["comment"] = None

    status = changes["status"]
    changes["user_status_pk"] = f"USER#{status}"

    transacts = []

    add_dynamodb_user_update_transact(transacts, cur_user, deltas={
        # Invalidate CDN cache for current user
        "cdn_cache_version": 1
    })

    if cur_user.id != user.id:
        add_dynamodb_user_update_transact(transacts, user, changes, {
            # Invalidate CDN cache for user
            "cnd_cache_version": 1,
        })

    # logger.debug(transacts)

    dynamodb_transact_write(transacts)

    # Invalidate CDN cache globally
    _drop_cdn_cache(
        _get_index_url(req),
        _get_user_urls(user, req),
        _get_users_urls(req),
    )


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

    # Handle pagination
    if len(results) == query_dto.limit:
        results[-1].offset = encode_offset(resp.get("LastEvaluatedKey"))

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


def get_latest_published_articles(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[Article]:
    query_dto = ArticleQueryDTO(limit=limit)
    return get_latest_articles(query_dto)


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


def should_show_popular_articles(latest_articles: list[Article], popular_articles: list[Article]) -> bool:
    """
    Show popular posts only if popular_posts differ from latest_posts.
    Comparison is based on post IDs.
    """
    latest_ids = [article.id for article in latest_articles]
    popular_ids = [article.id for article in popular_articles]

    # Show popular posts only if the lists are not exactly equal
    return latest_ids != popular_ids


def get_popular_published_articles(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[Article]:
    query_dto = ArticleQueryDTO(limit=limit)
    return get_popular_articles(query_dto)


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


def get_article_related_articles(article: Article) -> list[Article]:
    query_dto = ArticleQueryDTO()
    query_dto.tags = article.tags
    articles = get_popular_articles_by_tags(query_dto)
    return [p for p in articles if p.id != article.id]


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


def get_latest_article_comments(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[ArticleComment]:
    query_dto = ArticleCommentQueryDTO(limit=limit)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POST_COMMENTS_BY_CREATED_AT",
        key_condition_expr=Key("post_comment_pk").eq(f"POST_COMMENT"),
        map_fn=article_comment_from_dynamodb,
    )


def get_popular_articles_by_tags(query_dto: ArticleQueryDTO = None, cur_user: User = None) -> list[Article]:
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
    filtered_articles = [article for article in articles if set(query_dto.tags).issubset(set(article.tags))]
    if filtered_articles:
        filtered_articles[-1].offset = offset

    return filtered_articles


def update_article_status(article: Article, update_article_status_dto: UpdateArticleStatusDTO, cur_user: User, req) -> None:
    # logger.debug(f"update_post_status: post: {post}, cur_user: {cur_user}")
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_STATUS)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    if article.status == ArticleStatus.PUBLISHED:
        raise ArticleAlreadyPublishedError()

    changes = update_article_status_dto.changes()
    if not changes:
        return
    for k, v in changes.items():
        changes[k] = sanitize_html(v)
    if not "comment" in changes:
        changes["comment"] = None

    old_status = article.status
    status = changes["status"]
    now = utc_now()

    transacts = []

    article_owner = get_user(article.owner_id)
    add_dynamodb_user_update_transact(transacts, article_owner, deltas={
        # User post counters
        f"{old_status}_posts_count": -1,
        f"{status}_posts_count": 1,
        # Invalidate CDN cache for post owner
        f"cdn_cache_version": 1,
    })

    if status == ArticleStatus.PUBLISHED:
        if not article.published_at:
            changes["published_at"] = now
        if article_owner:
            changes["user_slug"] = article_owner.username
        # Upsert tags
        for tag in article.tags:
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

        # Create post tag combos
        from itertools import combinations
        for r in range(1, len(article.tags) + 1):
            for combo in combinations(sorted(article.tags), r):
                article_tag_combo_key = ("POST_TAG_COMBO#" + "#".join(combo), f"POST#{article.created_at}#{article.id}")
                add_dynamodb_put_transact(transacts, article_tag_combo_key, {"post_id": article.id})

    changes["post_status_pk"] = f"POST#{status}"
    changes["post_user_status_pk"] = f"POST#{article.user_id}#{status}"

    add_dynamodb_article_update_transact(transacts, article, changes)

    if cur_user.id != article_owner.id:
        add_dynamodb_user_update_transact(transacts, cur_user, deltas={
            # Invalidate CDN cache for current user
            "cdn_cache_version": 1
        })

    # logger.debug(transacts)

    dynamodb_transact_write(transacts)

    # Invalidate CDN cache globally
    _drop_cdn_cache(
        _get_article_urls(article, req),
        _get_user_urls(article_owner, req),
        _get_index_url(req),
        _get_articles_urls(req),
    )


def _get_user_urls(user: User, req) -> set[str]:
    return {
        get_user_url(req, user),
        get_static_user_url(req, user),
    }


def _get_index_url(req) -> str:
    return get_url(req, "index")


def _get_article_urls(article: Article, req) -> set[str]:
    return {
        get_article_url(req, article),
        get_static_article_url(req, article),
        get_url(req, "edit-article", article_id=article.id),
    }


def _get_users_urls(req) -> set[str]:
    urls = set()
    for _type in UserQueryType:
        urls.add(get_users_url(req, type=_type))
    return urls


def _get_articles_urls(req) -> set[str]:
    urls = set()
    for _type in ArticleQueryType:
        urls.add(get_articles_url(req, type=_type))
        for tag in get_article_tags(ArticleTagQueryDTO(limit=1000)):
            urls.add(get_articles_url(req, type=_type, tags=[tag.slug]))
    return urls


def _get_article_tag_urls(article_tag: ArticleTag, req) -> set[str]:
    return {
        get_article_tag_url(req, article_tag),
        get_url(req, "edit-article-tag", slug=article_tag.slug),
    }


def _get_user_article_urls(user: User, req) -> set[str]:
    urls = set()
    for article in get_latest_articles_by_user(user, ArticleQueryDTO(limit=1000)):
        urls.add(get_article_url(req, article))
        urls.add(get_static_article_url(req, article))
    return urls


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


def get_article_tags_by_prefix(query_dto: ArticleTagQueryDTO = None) -> list[ArticleTag]:
    if query_dto is None:
        query_dto = ArticleTagQueryDTO()
    resp = query_dynamodb_table(
        index_name="TAGS_BY_TYPE_NAME",
        key_condition_expr=Key("tag_type_pk").eq("POST_TAG") & Key("tag_name_sk").begins_with(query_dto.prefix),
        limit=query_dto.limit
    )
    items = resp.get("Items", [])
    # logger.debug(f"Tags: {items}")
    return [article_tag_from_dynamodb(item) for item in items]


def get_article_tags(query_dto: ArticleTagQueryDTO = None) -> list[ArticleTag]:
    if query_dto.prefix:
        return get_article_tags_by_prefix(query_dto)
    return get_popular_article_tags(query_dto)


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


def create_contact_message(message_dto: ContactMessageDTO, user: User = None) -> ContactMessage:
    user and verify_authorization(user, Permission.CREATE_CONTACT_MESSAGE)

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

        import httpx
        with httpx.Client() as client:
            token_resp = client.post(token_url, data=data, headers=headers)
            if token_resp.status_code != 200:
                logger.error(f"Token exchange failed: {token_resp.status_code} {token_resp.text}")
                raise CodeExchangeFailedError("Failed to exchange code")
            tokens = token_resp.json()
            # logger.debug(f"Cognito token response: {tokens}")

        id_token = tokens.get("id_token")
        if not id_token:
            raise InvalidTokenError("Missing id_token in Cognito response")
        from jose import jwt
        claims = jwt.get_unverified_claims(id_token)
        if claims.get("token_use") != "id":
            raise InvalidTokenError(f"Unexpected token_use: {claims.get('token_use')}")

        tokens = {"id_token": id_token}
        user_token = user_token_from_jwt_claims(claims, encode_offset(tokens))
    else:
        try:
            token_args = decode_offset(code) if code else {}
        except (ValueError, UnicodeError) as exc:
            raise InvalidCodeError("Invalid code") from exc
        user_token = get_dummy_user_token(**token_args)

    upsert_user_by_user_token(user_token)
    return user_token


def create_auth_jwt_token(token: UserTokenDTO) -> str:
    expires_in = get_auth_token_max_age()

    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_in)

    from jose import jwt
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


def get_redirect_url(req) -> str:
    redirect_url = req.query_params.get("redirect_url")

    if not redirect_url:
        referer = req.headers.get("referer")
        if referer:
            parsed = urlparse(referer)
            base_url = urlparse(get_base_url())

            # If referer has no netloc (relative path) → safe
            # If referer belongs to your domain → safe
            if not parsed.netloc or parsed.netloc == base_url.netloc:
                redirect_url = referer

    if not redirect_url:
        redirect_url = get_url(req, "index")

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


def get_popular_active_users(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[User]:
    query_dto = UserQueryDTO(limit=limit)
    return get_popular_users(query_dto)


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


def update_article_impression(article: Article, update_article_impression_dto: UpdateArticleImpressionDTO, cur_user: User,
                           req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_IMPRESSION, article)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    current_impression = find_article_impression(article, cur_user)
    current_action = current_impression.action if current_impression else None
    action = update_article_impression_dto.action
    article_impression_item = {
        "post_id": article.id,
        "user_id": cur_user.id,
        "action": action,
    }
    transacts = []

    article_deltas = {}
    article_imp_key = (f"POST#{article.id}", f"IMP#{cur_user.id}")

    if action == ArticleImpressionAction.LIKE:
        if current_action == ArticleImpressionAction.LIKE:
            add_dynamodb_delete_transact(transacts, article_imp_key)
            article_deltas["likes_count"] = -1
            article_deltas["rating_sk"] = compute_rating_sk(-1)
        elif current_action == ArticleImpressionAction.DISLIKE:
            add_dynamodb_update_transact(transacts, article_imp_key, {"action": ArticleImpressionAction.LIKE})
            article_deltas["dislikes_count"] = -1
            article_deltas["likes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(2)
        else:
            add_dynamodb_put_transact(transacts, article_imp_key,
                                      {**article_impression_item, "action": ArticleImpressionAction.LIKE},
                                      new_pk_only=True)
            article_deltas["likes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(1)

    elif action == ArticleImpressionAction.DISLIKE:
        if current_action == ArticleImpressionAction.DISLIKE:
            add_dynamodb_delete_transact(transacts, article_imp_key)
            article_deltas["dislikes_count"] = -1
            article_deltas["rating_sk"] = compute_rating_sk(1)
        elif current_action == ArticleImpressionAction.LIKE:
            add_dynamodb_update_transact(transacts, article_imp_key, {"action": ArticleImpressionAction.DISLIKE})
            article_deltas["likes_count"] = -1
            article_deltas["dislikes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(-2)
        else:
            add_dynamodb_put_transact(transacts, article_imp_key,
                                      {**article_impression_item, "action": ArticleImpressionAction.DISLIKE},
                                      new_pk_only=True)
            article_deltas["dislikes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(-1)

    add_dynamodb_article_update_transact(transacts, article, deltas=article_deltas)

    add_dynamodb_user_update_transact(transacts, cur_user, deltas={
        # Invalidate CDN cache for current user
        "cdn_cache_version": 1
    })

    if cur_user.id != article.owner_id:
        article_owner = get_user(article.owner_id)
        add_dynamodb_user_update_transact(transacts, article_owner, deltas={
            # Invalidate CDN cache for post owner
            "cdn_cache_version": 1
        })

    logger.debug(transacts)
    dynamodb_transact_write(transacts)

    # Invalidate CDN cache for post page globally
    _drop_cdn_cache(
        _get_article_urls(article, req),
    )


def update_user_impression(user: User, update_relation_dto: UpdateUserImpressionDTO, cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER_IMPRESSION, user)

    if user.status == UserStatus.BANNED:
        raise UserBannedError()

    if user.id == cur_user.id:
        return

    current_relation = find_user_impression(user, cur_user)
    current_action = current_relation.action if current_relation else None
    action = update_relation_dto.action
    relation_item = {
        "user_id": cur_user.id,
        "target_user_id": user.id,
        "action": action,
    }
    transacts = []

    cur_user_deltas = {
        # Invalidate CDN cache for current user
        "cdn_cache_version": 1,
    }
    user_deltas = {
        # Invalidate CDN cache for user
        "cdn_cache_version": 1,
    }
    relation_key = (f"USER#{cur_user.id}", f"REL#{user.id}")

    if action == UserImpressionAction.FOLLOW:
        if current_action == UserImpressionAction.FOLLOW:
            # Unfollow
            add_dynamodb_delete_transact(transacts, relation_key)
            cur_user_deltas["following_count"] = -1
            user_deltas["followers_count"] = -1
            user_deltas["rating_sk"] = compute_rating_sk(-1)
        elif current_action == UserImpressionAction.BLOCK:
            # Switching from block to follow
            add_dynamodb_update_transact(transacts, relation_key, {"action": UserImpressionAction.FOLLOW})
            cur_user_deltas["following_count"] = 1
            user_deltas["followers_count"] = 1
            user_deltas["rating_sk"] = compute_rating_sk(2)
        else:
            # New follow
            add_dynamodb_put_transact(transacts, relation_key, relation_item, new_pk_only=True)
            cur_user_deltas["following_count"] = 1
            user_deltas["followers_count"] = 1
            user_deltas["rating_sk"] = compute_rating_sk(1)

    elif action == UserImpressionAction.BLOCK:
        if current_action == UserImpressionAction.BLOCK:
            # Unblock
            add_dynamodb_delete_transact(transacts, relation_key)
            user_deltas["rating_sk"] = compute_rating_sk(1)
        elif current_action == UserImpressionAction.FOLLOW:
            # Switching from follow to block
            add_dynamodb_update_transact(transacts, relation_key, {"action": UserImpressionAction.BLOCK})
            cur_user_deltas["following_count"] = -1
            user_deltas["followers_count"] = -1
            user_deltas["rating_sk"] = compute_rating_sk(-2)
        else:
            # New block
            add_dynamodb_put_transact(transacts, relation_key, relation_item, new_pk_only=True)
            user_deltas["rating_sk"] = compute_rating_sk(-1)

    add_dynamodb_user_update_transact(transacts, cur_user, deltas=cur_user_deltas)
    add_dynamodb_user_update_transact(transacts, user, deltas=user_deltas)

    dynamodb_transact_write(transacts)

    # Invalidate CDN cache globally
    _drop_cdn_cache(
        _get_user_urls(user, req),
    )


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


def generate_sitemap(user: User, req) -> tuple[int, str]:
    verify_authorization(user, Permission.GENERATE_SITEMAP)

    today = datetime.utcnow().date().isoformat()

    def lastmod(ts_ms, fallback_ts_ms=None):
        ts_ms = ts_ms or fallback_ts_ms
        if not ts_ms:
            return today
        return datetime.fromtimestamp(
            float(ts_ms) / 1000,
            tz=timezone.utc
        ).date().isoformat()

    urls = []

    # Static
    def url(route: str) -> str:
        return get_url(req, route, True)

    urls.extend([
        (url("index"), today),
        (url("contacts"), today),
        (url("rules"), today),
        (url("terms"), today),
        (url("earn"), today),
    ])

    # Post lists
    def articles_url(tp: ArticleQueryType, tg: ArticleTag | None = None) -> str:
        return get_articles_url(req, type=tp, tags=[tg.name] if tg else [], full=True)

    for type_ in ArticleQueryType:
        urls.append((articles_url(type_), today))
        for tag in get_article_tags(ArticleTagQueryDTO(limit=1000)):
            if tag.articles_count > 0:
                urls.append((articles_url(type_, tag), today))

    # Posts
    def article_url(article: Article) -> str:
        return get_article_url(req, article, full=True)

    offset = None
    while articles := get_latest_articles(
            ArticleQueryDTO(status=ArticleStatus.PUBLISHED, limit=1000, offset=offset)):
        urls.extend([(article_url(article), lastmod(article.updated_at, article.created_at)) for article in articles])
        offset = articles[-1].offset
        if not offset:
            break

    # User lists
    def users_url(tp: UserQueryType) -> str:
        return get_users_url(req, type=tp, full=True)

    for type_ in UserQueryType:
        urls.append((users_url(type_), today))

    # Users
    def user_url(user_: User) -> str:
        return get_user_url(req, user_, full=True)

    offset = None
    while users := get_latest_users(
            UserQueryDTO(status=UserStatus.ACTIVE, limit=1000, offset=offset)):
        urls.extend([(user_url(user), lastmod(user.updated_at, user.created_at)) for user in users])
        offset = users[-1].offset
        if not offset:
            break

    # Save
    sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{''.join([f"""<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>""" for (loc, lastmod) in urls])}</urlset>"""

    sitemap_filename = save_public_file(
        FileDTO(content=sitemap_xml.encode("utf-8"), filename="sitemap.xml"),
        filename="sitemap.xml",
    )
    sitemap_url = get_static_url(req, sitemap_filename, full=True)

    # Invalidate CDN cache
    if is_prod():
        safe_execute("CF invalidation", _drop_cdn_cache, ["/sitemap.xml"])

    # Notify engines
    if is_prod():
        import httpx
        with httpx.Client(timeout=5.0) as client:
            safe_execute("Google SM notify", client.get, "https://www.google.com/ping", params={"sitemap": sitemap_url})
            safe_execute("Bing SM notify", client.get, "https://www.bing.com/ping", params={"sitemap": sitemap_url})

    return len(urls), sitemap_url


def get_cdn_cache_version(user: User) -> str:
    raw = f"{user.id}:{user.cdn_cache_version}"
    import hashlib
    return hashlib.md5(raw.encode()).hexdigest()


def extract_image_filename_dimensions(filename: str) -> tuple[int | None, int | None]:
    if not filename:
        return None, None

    match = re.search(r'_(\d+)x(\d+)(?:\.\w+)?$', filename)
    if match:
        return match.group(1), match.group(2)

    return None, None


def create_dummy_fixtures(req) -> None:
    if is_prod():
        return
    created_articles = []
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
        website="https://example.com",
        about=("Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the "
               "industry's standard dummy text ever since the 1500s, when an unknown printer took a galley of type "
               "and scrambled it to make a type specimen book. It has survived not only five centuries, but also the "
               "leap into electronic typesetting, remaining essentially unchanged. It was popularised in the 1960s "
               "with the release of Letraset sheets containing Lorem Ipsum passages, and more recently with desktop "
               "publishing software like Aldus PageMaker including versions of Lorem Ipsum."),
        address="1600 Pennsylvania Ave NW, Washington, DC 20500"
    )
    update_user(root_user, update_user_dto, root_user, req)
    articles = [
        ArticleDTO(
            title="Article title #111111111111111111111111",
            content="Article content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        ArticleDTO(
            title="Article title #22222222222222222222222",
            content="Article content #222222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        ArticleDTO(
            title="Article title #3333333333333333333333333",
            content="Article content #33333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for article in articles:
        created_article = create_article(article, root_user)
        update_article_status(created_article, UpdateArticleStatusDTO(status=ArticleStatus.PUBLISHED), root_user, req)
        created_articles.append(created_article)
    user_token2 = get_dummy_user_token(sub="p2", email="test2@example.com", name="Some test user")
    user2 = upsert_user_by_user_token(user_token2)
    created_users.append(user2)
    articles = [
        ArticleDTO(
            title="Article title #111111111111111111111111 for user 2",
            content="Article content #111111111111111111111111" * 150,
            tags=["tag3"]
        ),
        ArticleDTO(
            title="Article title #22222222222222222222222 for user 2",
            content="Article content #222222222222222222222222" * 150,
            tags=["tag2"]
        ),
        ArticleDTO(
            title="Article title #3333333333333333333333333 for user 2",
            content="Article content #33333333333333333333333" * 150,
            tags=["tag4"]
        ),
    ]
    for article in articles:
        created_article = create_article(article, user2)
        update_article_status(created_article, UpdateArticleStatusDTO(status=ArticleStatus.PUBLISHED), root_user, req)
        created_articles.append(created_article)
    user_token3 = get_dummy_user_token(sub="p3", email="test3@example.com")
    user3 = upsert_user_by_user_token(user_token3)
    created_users.append(user3)
    user_token4 = get_dummy_user_token(sub="p4", email="test4@example.com")
    user4 = upsert_user_by_user_token(user_token4)
    created_users.append(user4)

    comment_texts = [
        "This helped clarify the trade-offs. Thanks for writing it.",
        "Good walkthrough. I would like to see more examples around scaling this design.",
        "The section about operational limits is especially useful.",
        "Nice article. The diagrams and constraints make the approach easier to follow.",
    ]
    for article_index, article in enumerate(created_articles):
        commenters = [user for user in created_users if user.id != article.owner_id]
        for comment_index, user in enumerate(commenters[:2]):
            text = comment_texts[(article_index + comment_index) % len(comment_texts)]
            create_article_comment(article, ArticleCommentDTO(text=text), user, req)

    import random
    for user in created_users:
        for article in created_articles:
            update_article_impression(article, UpdateArticleImpressionDTO(
                action=ArticleImpressionAction.LIKE if random.random() < .5 else ArticleImpressionAction.DISLIKE), user,
                                   req)
        for user2 in created_users:
            if user.id != user2.id:
                update_user_impression(user, UpdateUserImpressionDTO(
                    action=UserImpressionAction.FOLLOW if random.random() < .5 else UserImpressionAction.BLOCK), user2,
                                       req)
    unpublished_articles = [
        ArticleDTO(
            title="Unpublished Article title #111111111111111111111111",
            content="Article content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        ArticleDTO(
            title="Unpublished Article title #22222222222222222222222",
            content="Article content #2222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        ArticleDTO(
            title="Unpublished Article title #3333333333333333333333333",
            content="Article content #333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for article in unpublished_articles:
        create_article(article, user2)
    rejected_articles = [
        ArticleDTO(
            title="Rejected Article title #111111111111111111111111",
            content="Article content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        ArticleDTO(
            title="Rejected Article title #22222222222222222222222",
            content="Article content #2222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        ArticleDTO(
            title="Rejected Article title #3333333333333333333333333",
            content="Article content #333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for article in rejected_articles:
        created_article = create_article(article, user3)
        update_article_status(created_article,
                           UpdateArticleStatusDTO(status=ArticleStatus.REJECTED, comment="Some rejection reason"),
                           root_user, req)
