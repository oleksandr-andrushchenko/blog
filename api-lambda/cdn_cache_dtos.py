from dataclasses import dataclass


@dataclass(slots=True)
class DropCDNCacheDTO:
    paths: list[str] | None = None

    def __post_init__(self):
        if self.paths is None:
            self.paths = ["/*"]
        elif isinstance(self.paths, list):
            if any(not isinstance(path, str) for path in self.paths):
                raise ValueError("each path must be a string")
            self.paths = [path.strip() for path in self.paths if path.strip()]
            self.paths = self.paths or ["/*"]
        else:
            raise ValueError("paths must be a list")

        if len(self.paths) > 3000:
            raise ValueError("CloudFront supports max 3000 paths per invalidation request")
        if any(not path.startswith("/") for path in self.paths):
            raise ValueError("each CloudFront path must start with '/'")
