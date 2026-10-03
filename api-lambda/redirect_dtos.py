from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(slots=True)
class UpsertRedirectDTO:
    path: str
    redirect_to: str

    def __post_init__(self):
        for field in ("path", "redirect_to"):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise ValueError(f"{field} must be a string")
            value = value.strip()
            if not value or len(value) > 2048:
                raise ValueError(f"{field} must contain between 1 and 2048 characters")
            setattr(self, field, value)

        source = urlsplit(self.path)
        if source.scheme or source.netloc or source.query or source.fragment or not self.path.startswith("/"):
            raise ValueError("path must be a root-relative URL path without a query or fragment")
        if "." in self.path:
            raise ValueError("path must not identify a file")
        if not self.redirect_to.startswith("/") or self.redirect_to.startswith("//"):
            raise ValueError("redirect_to must be a root-relative URL")
