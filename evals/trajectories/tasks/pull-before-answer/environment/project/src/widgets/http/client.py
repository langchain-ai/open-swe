from dataclasses import dataclass, field

from widgets import settings


@dataclass
class Client:
    base_url: str
    # Covers the whole request; connecting has its own, shorter limit.
    timeout: float = settings.REQUEST_TIMEOUT_SECONDS
    connect_timeout: float = settings.CONNECT_TIMEOUT_SECONDS
    headers: dict[str, str] = field(default_factory=lambda: {"User-Agent": settings.USER_AGENT})

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
