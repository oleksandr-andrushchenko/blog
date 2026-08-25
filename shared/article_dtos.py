from dataclasses import dataclass
from enum import StrEnum

from basic_dtos import BaseDTO, UNSET
from query_dtos import ArticleStatus


def _validate_tags(values):
    from shared_utils import to_kebab_case
    result = list(dict.fromkeys(to_kebab_case(value) for value in (values or [])))
    if not 1 <= len(result) <= 3:
        raise ValueError("tags must contain between 1 and 3 items")
    if any(not 2 <= len(value) <= 40 for value in result):
        raise ValueError("each tag must contain between 2 and 40 characters")
    return result


def _validate_title(value):
    if not 10 <= len(value) <= 500:
        raise ValueError("title must contain between 10 and 500 characters")


def _validate_content(value):
    if not 5_000 <= len(value) <= 100_000:
        raise ValueError("content must contain between 5000 and 50000 characters")


def _validate_comment_text(value):
    if not 1 <= len(value) <= 5_000:
        raise ValueError("text must contain between 1 and 5000 characters")


@dataclass(slots=True)
class ArticleDTO(BaseDTO):
    title: str
    content: str
    tags: list[str]

    def __post_init__(self):
        _validate_title(self.title)
        _validate_content(self.content)
        self.tags = _validate_tags(self.tags)


@dataclass(slots=True)
class UpdateArticleDTO(BaseDTO):
    title: str | None | object = UNSET
    content: str | None | object = UNSET
    tags: list[str] | None | object = UNSET

    def __post_init__(self):
        if self.title is not UNSET:
            if self.title is None:
                raise ValueError("title must contain between 10 and 500 characters")
            _validate_title(self.title)
        if self.content is not UNSET:
            if self.content is None:
                raise ValueError("content must contain between 5000 and 50000 characters")
            _validate_content(self.content)
        if self.tags is not UNSET:
            self.tags = _validate_tags(self.tags)


@dataclass(slots=True)
class UpdateTagDTO(BaseDTO):
    name: str | None | object = UNSET
    image_action: str | None | object = UNSET
    image_filename: str | None | object = UNSET

    def __post_init__(self):
        if self.name is not UNSET:
            if self.name is None or not 2 <= len(self.name) <= 40:
                raise ValueError("name must contain between 2 and 40 characters")
        if self.image_action is not UNSET and self.image_action not in (None, "delete", "replace", "keep"):
            raise ValueError("invalid image action")


@dataclass(slots=True)
class UpdateArticleStatusDTO(BaseDTO):
    status: ArticleStatus
    comment: str | None = None

    def __post_init__(self):
        self.status = ArticleStatus(self.status)
        if self.status == ArticleStatus.REJECTED and not self.comment:
            raise ValueError("Comment is required when rejecting an article")


class ArticleImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class UpdateArticleImpressionDTO(BaseDTO):
    action: ArticleImpressionAction

    def __post_init__(self):
        self.action = ArticleImpressionAction(self.action)


@dataclass(slots=True)
class ArticleCommentDTO(BaseDTO):
    text: str

    def __post_init__(self):
        _validate_comment_text(self.text)


@dataclass(slots=True)
class UpdateArticleCommentDTO(BaseDTO):
    text: str | None | object = UNSET

    def __post_init__(self):
        if self.text is not UNSET:
            if self.text is None:
                raise ValueError("text must contain between 1 and 5000 characters")
            _validate_comment_text(self.text)


class ArticleCommentImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class UpdateArticleCommentImpressionDTO(BaseDTO):
    action: ArticleCommentImpressionAction

    def __post_init__(self):
        self.action = ArticleCommentImpressionAction(self.action)
