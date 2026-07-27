from dataclasses import asdict, dataclass, field
from enum import StrEnum


def _limit(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= value <= 20 and value != 1000:
        raise ValueError("limit must be between 1 and 20")
    return value


@dataclass(slots=True)
class BaseQueryDTO:
    DEFAULT_OFFSET = None
    DEFAULT_LIMIT = 20

    offset: str | None = None
    limit: int = 20

    def __post_init__(self):
        self.limit = _limit(self.limit)

    def get_dict(self, rewrite=None):
        result = asdict(self)
        result.update(rewrite or {})
        return {k: v.value if isinstance(v, StrEnum) else v for k, v in result.items()}

    def has_params(self):
        return self.offset is not None or self.limit != self.DEFAULT_LIMIT


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


@dataclass(slots=True)
class ArticleTagQueryDTO(BaseQueryDTO):
    prefix: str | None = None

    def __post_init__(self):
        BaseQueryDTO.__post_init__(self)
        if self.prefix is not None and not 1 <= len(self.prefix) <= 40:
            raise ValueError("prefix must contain between 1 and 40 characters")

    def has_params(self):
        return BaseQueryDTO.has_params(self) or self.prefix is not None


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
    type: ArticleQueryType = ArticleQueryType.LATEST
    status: ArticleStatus = ArticleStatus.PUBLISHED

    def __post_init__(self):
        BaseQueryDTO.__post_init__(self)
        self.type = ArticleQueryType(self.type)
        self.status = ArticleStatus(self.status)

    def has_params(self):
        return BaseQueryDTO.has_params(self) or bool(
            self.tags) or self.type != self.DEFAULT_TYPE or self.status != self.DEFAULT_STATUS


@dataclass(slots=True)
class ArticleCommentQueryDTO(BaseQueryDTO):
    pass
