"""The tools a dashboard page offers Robin, and the page session a run sends their commands to.

A component on the page declares each tool to the WebMCP tool contract. With every
message the chat sends what its page offers (``page_tools``) and the browser tab's own
page session id (``page_session``). The run gets one tool per offer, under
``PAGE_TOOL_PREFIX``, that queues a command for that page session
(``openswe.tools.page_commands``).
"""

import json
from typing import Annotated, Final
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError, field_validator

from openswe.client_tools import ClientToolSpec

# Plugin tools are ``<plugin-name>__…`` and plugin names have no underscore, so a
# page tool, whose own name starts with a letter, can never pass for one; no
# built-in tool starts with it either.
PAGE_TOOL_PREFIX: Final = "page_"
MAX_PAGE_TOOLS: Final = 32
_MAX_SCHEMA_CHARS: Final = 16_000
_NO_INPUT: Final[dict[str, JsonValue]] = {"type": "object", "properties": {}}


class PageToolOffer(BaseModel):
    """One tool as the page offers it, under the page's own name for it."""

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,58}$")
    description: str = Field(min_length=1, max_length=2_000)
    parameters: dict[str, JsonValue] = Field(default_factory=_NO_INPUT.copy)

    @field_validator("parameters")
    @classmethod
    def _small_schema(cls, schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(json.dumps(schema)) > _MAX_SCHEMA_CHARS:
            raise ValueError(f"an input schema may be at most {_MAX_SCHEMA_CHARS} characters")
        return schema

    def spec(self) -> ClientToolSpec:
        """The tool as the agent sees it, under the page prefix."""
        return ClientToolSpec(
            kind="function",
            name=f"{PAGE_TOOL_PREFIX}{self.name}",
            description=self.description,
            parameters=self.parameters,
        )

    @staticmethod
    def page_name(name: str) -> str | None:
        """The page's own name for the agent's tool ``name``, if it is a page tool."""
        return name.removeprefix(PAGE_TOOL_PREFIX) if name.startswith(PAGE_TOOL_PREFIX) else None


_offers = TypeAdapter(Annotated[list[PageToolOffer], Field(max_length=MAX_PAGE_TOOLS)])


class PageSession(BaseModel):
    """The browser tab a run queues page commands for, and the tools its page offered."""

    id: UUID
    # The person whose page it is (``users.id``): only they read and run its commands.
    owner: UUID
    tools: list[ClientToolSpec]

    @classmethod
    def offered(cls, session: object, owner: UUID, tools: object) -> PageSession:
        """What a message sent from a page carries; 400 for an offer the backend cannot hold."""
        try:
            return cls(
                id=UUID(str(session)),
                owner=owner,
                tools=[offer.spec() for offer in _offers.validate_python(tools)],
            )
        except (ValueError, ValidationError) as exc:
            raise HTTPException(400, f"invalid page tools: {exc}") from exc
