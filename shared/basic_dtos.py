from dataclasses import dataclass, fields
from datetime import datetime

UNSET = object()


@dataclass(slots=True)
class BaseDTO:
    def get_changes(self, target=None):
        changes = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if value is UNSET:
                continue
            if target is not None and hasattr(target, field.name) and getattr(target, field.name) == value:
                continue
            changes[field.name] = value
        return changes


from validation import validate_email_address


@dataclass(slots=True)
class UserTokenDTO(BaseDTO):
    sub: str
    iss: str
    email: str | None = None
    name: str | None = None
    username: str | None = None
    iat: datetime | None = None
    exp: datetime | None = None
    max_age: int | None = None
    aud: str | list[str] | None = None
    plain_token: str | None = None


@dataclass(slots=True)
class FileDTO(BaseDTO):
    content: bytes
    filename: str

    @property
    def size(self):
        return len(self.content)

    @property
    def extension(self):
        import filetype
        kind = filetype.guess(self.content)
        return kind.extension if kind else None


@dataclass(slots=True)
class ImageFileDTO(FileDTO):
    MAX_IMAGE_SIZE = 2 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif"}

    def __post_init__(self):
        if self.size > self.MAX_IMAGE_SIZE:
            raise ValueError(f"File too large: {self.size} bytes, max {self.MAX_IMAGE_SIZE}")
        if self.extension not in self.ALLOWED_IMAGE_EXTENSIONS:
            raise ValueError(f"Invalid image type: {self.extension}")


@dataclass(slots=True)
class ContactMessageDTO(BaseDTO):
    name: str
    email: str
    message: str

    def __post_init__(self):
        if not 2 <= len(self.name) <= 100:
            raise ValueError("name must contain between 2 and 100 characters")
        self.email = validate_email_address(self.email)
        if not 5 <= len(self.message) <= 1000:
            raise ValueError("message must contain between 5 and 1000 characters")
