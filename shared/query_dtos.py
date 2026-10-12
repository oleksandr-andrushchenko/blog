import base64
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from validation import validate_category_slug


@dataclass(slots=True)
class BaseQueryDTO:
    DEFAULT_OFFSET = None
    DEFAULT_LIMIT = 40

    offset: str | None = DEFAULT_OFFSET
    limit: int = DEFAULT_LIMIT

    def __post_init__(self):
        self.limit = _limit(self.limit)

    def get_dict(self, rewrite=None):
        result = asdict(self)
        result.update(rewrite or {})
        return {k: v.value if isinstance(v, StrEnum) else v for k, v in result.items()}

    def has_params(self):
        return self.offset is not None or self.limit != self.DEFAULT_LIMIT


def _limit(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= value <= BaseQueryDTO.DEFAULT_LIMIT and value != 1000:
        raise ValueError("limit must be between 1 and " + str(BaseQueryDTO.DEFAULT_LIMIT))
    return value


class UserQueryType(StrEnum):
    LATEST = "latest"
    POPULAR = "popular"


class UserStatus(StrEnum):
    ACTIVE = "active"
    BANNED = "banned"


@dataclass(slots=True)
class UserQueryDTO(BaseQueryDTO):
    DEFAULT_TYPE = UserQueryType.LATEST
    DEFAULT_STATUS = UserStatus.ACTIVE

    type: UserQueryType = UserQueryType.LATEST
    status: UserStatus = UserStatus.ACTIVE

    def __post_init__(self):
        BaseQueryDTO.__post_init__(self)
        self.type = UserQueryType(self.type)
        self.status = UserStatus(self.status)

    def has_params(self):
        return BaseQueryDTO.has_params(self) or self.type != self.DEFAULT_TYPE or self.status != self.DEFAULT_STATUS


class TagQueryType(StrEnum):
    LATEST = "latest"
    POPULAR = "popular"


@dataclass(slots=True)
class TagQueryDTO(BaseQueryDTO):
    DEFAULT_TYPE = TagQueryType.LATEST

    type: TagQueryType = DEFAULT_TYPE
    prefix: str | None = None

    def __post_init__(self):
        BaseQueryDTO.__post_init__(self)
        self.type = TagQueryType(self.type)
        if self.prefix is not None and not 1 <= len(self.prefix) <= 40:
            raise ValueError("prefix must contain between 1 and 40 characters")

    def has_params(self):
        return BaseQueryDTO.has_params(self) or self.type != self.DEFAULT_TYPE or self.prefix is not None


class ArticleQueryType(StrEnum):
    LATEST = "latest"
    POPULAR = "popular"


class ArticleStatus(StrEnum):
    UNPUBLISHED = "unpublished"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass(slots=True)
class ArticleQueryDTO(BaseQueryDTO):
    DEFAULT_TYPE = ArticleQueryType.LATEST
    DEFAULT_STATUS = ArticleStatus.PUBLISHED

    tags: list[str] = field(default_factory=list)
    category: str | None = None
    type: ArticleQueryType = ArticleQueryType.LATEST
    status: ArticleStatus = ArticleStatus.PUBLISHED

    def __post_init__(self):
        BaseQueryDTO.__post_init__(self)
        self.tags = sorted({tag for tag in self.tags if tag})
        self.type = ArticleQueryType(self.type)
        self.status = ArticleStatus(self.status)
        if self.category is not None:
            self.category = validate_category_slug(self.category)
        if self.offset and self.category:
            try:
                cursor = json.loads(base64.urlsafe_b64decode(self.offset).decode())
            except (ValueError, UnicodeError) as exc:
                raise ValueError("offset must be a valid pagination cursor") from exc
            sort_key = "rating_sk" if self.type == ArticleQueryType.POPULAR else "created_at"
            expected_keys = {"pk", "sk", "post_category_status_pk", sort_key}
            if (not isinstance(cursor, dict) or set(cursor) != expected_keys
                    or cursor.get("post_category_status_pk") != f"POST#{self.category}#{self.status}"
                    or not isinstance(cursor.get("pk"), str) or not cursor["pk"].startswith("POST#")
                    or cursor.get("sk") != "META"
                    or isinstance(cursor.get(sort_key), bool)
                    or not isinstance(cursor.get(sort_key), (int, float))):
                raise ValueError("offset does not match this article category, status, or sort order")

    def has_params(self):
        return BaseQueryDTO.has_params(self) or bool(
            self.tags) or self.category is not None or self.type != self.DEFAULT_TYPE or self.status != self.DEFAULT_STATUS


@dataclass(slots=True)
class ArticleListingQueryDTO(ArticleQueryDTO):
    """Article feed query, whose cursor must match the selected index."""

    def __post_init__(self):
        ArticleQueryDTO.__post_init__(self)
        if not self.offset or self.category:
            return
        try:
            cursor = json.loads(base64.urlsafe_b64decode(self.offset).decode())
        except (ValueError, UnicodeError) as exc:
            raise ValueError("offset must be a valid pagination cursor") from exc
        if self.tags and self.type == ArticleQueryType.LATEST:
            valid = (isinstance(cursor, dict) and set(cursor) == {"pk", "sk"}
                     and cursor.get("pk") == "POST_TAG_COMBO#" + "#".join(self.tags)
                     and isinstance(cursor.get("sk"), str) and cursor["sk"].startswith("POST#"))
        else:
            sort_key = "rating_sk" if self.type == ArticleQueryType.POPULAR else "created_at"
            valid = (isinstance(cursor, dict) and set(cursor) == {"pk", "sk", "post_status_pk", sort_key}
                     and cursor.get("post_status_pk") == f"POST#{self.status}"
                     and isinstance(cursor.get("pk"), str) and cursor["pk"].startswith("POST#")
                     and cursor.get("sk") == "META"
                     and not isinstance(cursor.get(sort_key), bool)
                     and isinstance(cursor.get(sort_key), (int, float)))
        if not valid:
            raise ValueError("offset does not match this article listing, status, or sort order")


@dataclass(slots=True)
class ArticleCommentQueryDTO(BaseQueryDTO):
    pass
