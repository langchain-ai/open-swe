from dataclasses import dataclass

DEFAULT_RETRIES = 4
DEFAULT_BACKOFF_SECONDS = 0.5


@dataclass
class Client:
    base_url: str
    retries: int = DEFAULT_RETRIES
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
