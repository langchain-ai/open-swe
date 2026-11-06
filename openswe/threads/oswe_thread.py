"""Open SWE's typed view of a LangGraph thread."""

import hashlib
from typing import Self

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, ConfigDict, JsonValue
from runtime_api.provenance import Provenance


class OsweThreadMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    type: str = ""
    content: JsonValue = None
    additional_kwargs: dict[str, JsonValue] = {}

    @property
    def from_person(self) -> bool:
        provenance = Provenance.of(self.model_dump())
        return provenance is not None and provenance.human


class OsweThreadValues(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[OsweThreadMessage] = []


class OsweThreadState(BaseModel):
    """Open SWE's typed view of a LangGraph thread's saved state."""

    model_config = ConfigDict(extra="ignore")

    values: OsweThreadValues | None = None

    @classmethod
    async def get(cls, client: LangGraphClient, thread_id: str) -> Self:
        return cls.model_validate(await client.threads.get_state(thread_id))

    @property
    def messages(self) -> list[OsweThreadMessage]:
        return self.values.messages if self.values else []

    @property
    def latest_human_message_key(self) -> str:
        """A key for the newest message a person sent, which changes with each new one.

        The runs a thread may start for itself are counted against it.
        """
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if message.from_person:
                identity = message.id or repr(message.content)
                return hashlib.sha256(f"{index}:{identity}".encode()).hexdigest()
        return "no-human-message"
