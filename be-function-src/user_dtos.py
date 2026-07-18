from dataclasses import asdict, dataclass
from enum import StrEnum
import re

from query_dtos import UserStatus
from validation import validate_http_url


@dataclass(slots=True)
class UpdateUserDTO:
    name: str
    username: str | None = None
    avatar_action: str | None = None
    avatar_filename: str | None = None
    headline: str | None = None
    about: str | None = None
    website: str | None = None
    address: str | None = None
    github_username: str | None = None
    bmc_username: str | None = None

    def __post_init__(self):
        if not 1 <= len(self.name) <= 100:
            raise ValueError("invalid name length")
        if self.username is not None:
            self.username = self.username.strip()
            if not 3 <= len(self.username) <= 30 or not re.fullmatch(r"[a-z0-9-]+", self.username):
                raise ValueError("invalid username")
            if self.username.startswith("-") or self.username.endswith("-") or "--" in self.username:
                raise ValueError("invalid username")
        if self.avatar_action not in (None, "delete", "replace", "keep"):
            raise ValueError("invalid avatar action")
        for value, maximum in ((self.headline, 150), (self.about, 2000), (self.website, 255), (self.address, 255), (self.github_username, 39), (self.bmc_username, 50)):
            if value is not None and len(value) > maximum:
                raise ValueError("value is too long")
        self.website = validate_http_url(self.website)
        if self.github_username is not None:
            value = self.github_username.split("github.com/", 1)[-1].strip("/").split("/")[0].lower()
            if not re.fullmatch(r"[a-z0-9-]+", value) or value.startswith("-") or value.endswith("-") or "--" in value:
                raise ValueError("invalid GitHub username")
            self.github_username = value
        if self.bmc_username is not None:
            value = self.bmc_username.split("buymeacoffee.com/", 1)[-1].strip("/").lower()
            if not re.fullmatch(r"[a-z0-9.]+", value) or value.startswith(".") or value.endswith(".") or ".." in value:
                raise ValueError("invalid BMC username")
            self.bmc_username = value

    def changes(self):
        return asdict(self)


class UserImpressionAction(StrEnum):
    FOLLOW = "follow"
    BLOCK = "block"


@dataclass(slots=True)
class UpdateUserStatusDTO:
    status: UserStatus
    comment: str | None = None

    def __post_init__(self):
        self.status = UserStatus(self.status)
        if self.status == UserStatus.BANNED and not self.comment:
            raise ValueError("Comment is required when banning a user")

    def changes(self):
        return asdict(self)


@dataclass(slots=True)
class UpdateUserImpressionDTO:
    action: UserImpressionAction

    def __post_init__(self):
        self.action = UserImpressionAction(self.action)
