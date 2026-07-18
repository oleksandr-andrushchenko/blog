from dataclasses import asdict, dataclass
from enum import StrEnum

from query_dtos import PostStatus


def _tags(values):
    from utils import to_kebab_case
    result = list(dict.fromkeys(to_kebab_case(value) for value in (values or [])))
    if not 1 <= len(result) <= 3:
        raise ValueError("tags must contain between 1 and 3 items")
    if any(not 2 <= len(value) <= 40 for value in result):
        raise ValueError("each tag must contain between 2 and 40 characters")
    return result


@dataclass(slots=True)
class PostDTO:
    title: str
    content: str
    tags: list[str]

    def __post_init__(self):
        if not 10 <= len(self.title) <= 500:
            raise ValueError("title must contain between 10 and 500 characters")
        if not 5_000 <= len(self.content) <= 50_000:
            raise ValueError("content must contain between 5000 and 50000 characters")
        self.tags = _tags(self.tags)

    def changes(self):
        return asdict(self)


class UpdatePostDTO(PostDTO):
    pass


@dataclass(slots=True)
class UpdatePostTagDTO:
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
class UpdatePostStatusDTO:
    status: PostStatus
    comment: str | None = None

    def __post_init__(self):
        self.status = PostStatus(self.status)
        if self.status == PostStatus.REJECTED and not self.comment:
            raise ValueError("Comment is required when rejecting a post")

    def changes(self):
        return asdict(self)


class PostImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class UpdatePostImpressionDTO:
    action: PostImpressionAction

    def __post_init__(self):
        self.action = PostImpressionAction(self.action)


@dataclass(slots=True)
class PostCommentDTO:
    text: str

    def __post_init__(self):
        if not 1 <= len(self.text) <= 5_000:
            raise ValueError("text must contain between 1 and 5000 characters")

    def changes(self):
        return asdict(self)


class UpdatePostCommentDTO(PostCommentDTO):
    pass


class PostCommentImpressionAction(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


@dataclass(slots=True)
class UpdatePostCommentImpressionDTO:
    action: PostCommentImpressionAction

    def __post_init__(self):
        self.action = PostCommentImpressionAction(self.action)
