from dataclasses import asdict, dataclass
from enum import StrEnum

from query_dtos import ArticleStatus


def _tags(values):
    from utils import to_kebab_case
    result = list(dict.fromkeys(to_kebab_case(value) for value in (values or [])))
    if not 1 <= len(result) <= 3:
        raise ValueError("tags must contain between 1 and 3 items")
    if any(not 2 <= len(value) <= 40 for value in result):
        raise ValueError("each tag must contain between 2 and 40 characters")
    return result


@dataclass(slots=True)
class ArticleDTO:
    title: str
    content: str
    tags: list[str]

    def __post_init__(self):
        if not 10 <= len(self.title) <= 500:
            raise ValueError("title must contain between 10 and 500 characters")
        if not 5_000 <= len(self.content) <= 100_000:
            raise ValueError("content must contain between 5000 and 50000 characters")
        self.tags = _tags(self.tags)

    def changes(self):
        return asdict(self)


class UpdateArticleDTO(ArticleDTO):
    pass


@dataclass(slots=True)
class UpdateArticleTagDTO:
    name: str
    image_action: str | None = None
    image_filename: str | None = None

    def __post_init__(self):
        if not 2 <= len(self.name) <= 40:
            raise ValueError("name must contain between 2 and 40 characters")
        if self.image_action not in (None, "delete", "replace", "keep"):
            raise ValueError("invalid image action")

    def changes(self):
        return asdict(self)


@dataclass(slots=True)
class UpdateArticleStatusDTO:
    status: ArticleStatus
    comment: str | None = None

    def __post_init__(self):
        self.status = ArticleStatus(self.status)
        if self.status == ArticleStatus.REJECTED and not self.comment:
            raise ValueError("Comment is required when rejecting an article")

    def changes(self):
        return asdict(self)


class ArticleImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class UpdateArticleImpressionDTO:
    action: ArticleImpressionAction

    def __post_init__(self):
        self.action = ArticleImpressionAction(self.action)


@dataclass(slots=True)
class ArticleCommentDTO:
    text: str

    def __post_init__(self):
        if not 1 <= len(self.text) <= 5_000:
            raise ValueError("text must contain between 1 and 5000 characters")

    def changes(self):
        return asdict(self)


class UpdateArticleCommentDTO(ArticleCommentDTO):
    pass


class ArticleCommentImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class UpdateArticleCommentImpressionDTO:
    action: ArticleCommentImpressionAction

    def __post_init__(self):
        self.action = ArticleCommentImpressionAction(self.action)
