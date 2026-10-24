"""Which box a thread is bound to, and the LangSmith it lives in."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

SANDBOX_API_METADATA_KEY = "sandbox_api"


@dataclass(frozen=True, slots=True)
class SandboxRef:
    """A box by id, and the LangSmith API root it lives at.

    ``api`` is ``None`` for a box bound before regions were recorded, which lives in
    the deployment's own LangSmith, and for providers other than LangSmith.
    """

    id: str
    api: str | None = None

    @classmethod
    def of(cls, metadata: Mapping[str, object]) -> Self | None:
        """The box a thread's metadata binds it to, if any."""
        sandbox_id = metadata.get("sandbox_id")
        if not isinstance(sandbox_id, str) or not sandbox_id:
            return None
        api = metadata.get(SANDBOX_API_METADATA_KEY)
        return cls(sandbox_id, api if isinstance(api, str) and api else None)

    def metadata(self) -> dict[str, object]:
        """The thread metadata that binds a thread to this box."""
        return {"sandbox_id": self.id, SANDBOX_API_METADATA_KEY: self.api}
