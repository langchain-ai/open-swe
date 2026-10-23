"""Tools a Responses client declares and runs itself, as a run's config carries them."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue
from runtime_api.operations import CLIENT_CALL_KEY

type ClientToolKind = Literal["function", "custom"]

PENDING_ARTIFACT_KEY = CLIENT_CALL_KEY
CUSTOM_TOOL_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"input": {"type": "string", "description": "The tool's raw text input."}},
    "required": ["input"],
}
# The client runs these itself, so the sandbox's own copies would only compete with them.
CLIENT_OWNED_SERVER_TOOLS = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "background_execute",
    }
)


class ClientToolSpec(BaseModel):
    kind: ClientToolKind
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    parameters: dict[str, JsonValue]

    @staticmethod
    def result_message_id(call_id: str) -> str:
        """The id of a call's placeholder result, which the client's real result replaces."""
        return f"client-tool-result-{call_id}"
