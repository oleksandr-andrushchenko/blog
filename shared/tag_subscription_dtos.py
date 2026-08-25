import json
from dataclasses import dataclass

from basic_dtos import BaseDTO


@dataclass(slots=True)
class TagSubscriptionDTO(BaseDTO):
    tags: list[str]

    def __post_init__(self):
        from shared_utils import sanitize_tags

        if isinstance(self.tags, str):
            try:
                self.tags = json.loads(self.tags)
            except json.JSONDecodeError:
                self.tags = [tag.strip() for tag in self.tags.split(",") if tag.strip()]

        self.tags = sorted(set(sanitize_tags(self.tags)))
        if not 1 <= len(self.tags) <= 3:
            raise ValueError("tag subscriptions must contain between 1 and 3 tags")
        if any(not 2 <= len(tag) <= 40 for tag in self.tags):
            raise ValueError("each tag must contain between 2 and 40 characters")


@dataclass(slots=True)
class TagSubscription:
    id: str
    user_id: str
    tags: list[str]
    created_at: int

    @property
    def key(self) -> str:
        return "#".join(self.tags)
