"""The wire shapes a bridge request travels in.

The desktop reads ``params`` and writes ``result``; kept free of the agent stack so
the web app can import them.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

BridgeMethod = Literal["local_action"]

JsonObject = dict[str, JsonValue]


class LocalActionParams(BaseModel):
    """What the desktop is asked to do. Unknown keys are rejected: the desktop reads these."""

    model_config = ConfigDict(extra="forbid")

    expires_at: float = 0
    operation: Literal["read", "write", "execute", "import"]
    path: str | None = Field(default=None, max_length=4096)
    content: str | None = Field(default=None, max_length=65536)
    command: str | None = Field(default=None, max_length=65536)
    cwd: str | None = Field(default=None, max_length=4096)
    timeout: int = Field(default=120, ge=1, le=300)
    # The thread asking, which the desktop scopes grants to and names in its dialog.
    thread_id: str | None = None
    thread_title: str | None = Field(default=None, max_length=200)
    # The runtime's review: only a cleared call may run inside a grant without asking.
    cleared: bool = False
    concern: str | None = Field(default=None, max_length=64)

    def dump(self) -> JsonObject:
        return self.model_dump(mode="json")
