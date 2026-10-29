"""Open SWE's typed view of a LangGraph thread."""

import hashlib
from typing import Self

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, ConfigDict, JsonValue

from openswe.input_messages import message_sender_id


class OsweThreadMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    content: JsonValue = None

    @property
    def from_person(self) -> bool:
        return message_sender_id(self.content, kind="human") is not None


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
