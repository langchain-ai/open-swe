"""Slacklets the agent defines at runtime: its own program decides their state and look.

The agent writes a small JavaScript program defining three pure functions:

- ``init(params)`` returns the first state;
- ``apply(state, command)`` returns the state after ``command`` (``{op, params}``),
  or throws to refuse it;
- ``render(state)`` returns ``{text, blocks}``: the notification text and the
  Block Kit blocks the slacklet message shows.

Programs run in an isolated QuickJS context with no network, filesystem, clock
or randomness, under memory and time limits, so replaying a slacklet's commands
always rebuilds the same state. Their blocks are checked against the Block Kit
subset every slacklet surface renders, in Slack and on the web.
"""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID, uuid7

import quickjs_rs
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from sqlalchemy import ForeignKey, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.run_config import RunConfig
from openswe.slack.blocks import (
    Block,
    ContextBlock,
    SectionBlock,
    TextObject,
    context,
    divider,
    mrkdwn,
    plain_text,
)
from openswe.slacklets.kind import SlackletCommand, SlackletRefused, SlackletState
from openswe.slacklets.store import SlackletKind
from openswe.utils.json_types import JsonObject

SOURCE_MAX_CHARS = 32_000
STATE_MAX_CHARS = 64_000
_MEMORY_LIMIT = 16 * 1024 * 1024
_TIMEOUT_SECONDS = 0.5
_MAX_BLOCKS = 40
# Replays must rebuild the same state, so programs get no clock or randomness.
_PRELUDE = """
"use strict";
globalThis.Date = undefined;
Math.random = () => { throw new Error("slacklet programs must be deterministic"); };
void 0;
"""


class SlackletProgramError(ValueError):
    """The agent's program failed, refused a command, or returned something unusable."""


class _Text(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["mrkdwn", "plain_text"]
    text: str = Field(min_length=1, max_length=3000)


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["section"]
    text: _Text


class _Context(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["context"]
    elements: list[_Text] = Field(min_length=1, max_length=10)


class _Divider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["divider"]


class _Rendered(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=3000)
    blocks: list[Annotated[_Section | _Context | _Divider, Field(discriminator="type")]] = Field(
        min_length=1, max_length=_MAX_BLOCKS
    )


_RENDERED = TypeAdapter(_Rendered)


class SlackletProgram:
    """One agent-written program, run in a fresh isolated QuickJS context per call."""

    def __init__(self, source: str) -> None:
        if len(source) > SOURCE_MAX_CHARS:
            raise SlackletProgramError(f"The program is over {SOURCE_MAX_CHARS} characters")
        self.source = source

    def _call(self, function: str, *args: JsonValue) -> JsonValue:
        call = f"JSON.stringify({function}({', '.join(json.dumps(arg) for arg in args)}))"
        runtime = quickjs_rs.Runtime(memory_limit=_MEMORY_LIMIT)
        try:
            js = runtime.new_context(timeout=_TIMEOUT_SECONDS)
            try:
                js.eval(_PRELUDE)
                js.eval(f"{self.source}\n;void 0;", filename="slacklet.js")
                result = js.eval(call)
            finally:
                js.close()
        except quickjs_rs.QuickJSError as exc:
            raise SlackletProgramError(f"{function} failed: {exc}") from None
        finally:
            runtime.close()
        if not isinstance(result, str) or len(result) > STATE_MAX_CHARS:
            raise SlackletProgramError(
                f"{function} must return JSON under {STATE_MAX_CHARS} characters"
            )
        return json.loads(result)

    def init(self, params: JsonObject) -> JsonValue:
        return self._call("init", params)

    def apply(self, data: JsonValue, op: str, params: JsonObject) -> JsonValue:
        return self._call("apply", data, {"op": op, "params": params})

    def render(self, data: JsonValue) -> _Rendered:
        try:
            return _RENDERED.validate_python(self._call("render", data))
        except ValidationError as exc:
            raise SlackletProgramError(
                f"render must return {{text, blocks}} using only section, context and "
                f"divider blocks: {exc.errors(include_url=False)}"
            ) from None


class SlackletDefinition(Base):
    """An agent-written slacklet program, stored once per distinct source."""

    __tablename__ = "slacklet_definition"

    name: Mapped[str]
    source: Mapped[str]
    source_sha256: Mapped[str] = mapped_column(unique=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def save(cls, name: str, source: str, *, created_by: UUID | None) -> Self:
        """The stored definition for ``source``, saving it first if it is new."""
        digest = hashlib.sha256(source.encode()).hexdigest()
        async with postgres.session() as session:
            await session.execute(
                insert(cls)
                .values(
                    id=uuid7(),
                    name=name,
                    source=source,
                    source_sha256=digest,
                    created_by=created_by,
                    updated_by=created_by,
                )
                .on_conflict_do_nothing(index_elements=[cls.source_sha256])
            )
            saved = await session.scalar(select(cls).where(cls.source_sha256 == digest))
        if saved is None:
            raise LookupError("The slacklet definition vanished right after it was saved")
        return saved

    @classmethod
    async def get(cls, definition_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.get(cls, definition_id)

    def kind(self) -> SlackletKind[ProgramState, ProgramCommand]:
        """This program as a slacklet kind; its slacklets' subject is this definition's id."""
        program = SlackletProgram(self.source)
        name = self.name

        def apply(state: ProgramState | None, command: ProgramCommand) -> ProgramState:
            if state is None:
                if command.op != "init":
                    raise SlackletProgramError(f"{command.op} needs an initialized slacklet")
                data = program.init(command.params)
            else:
                if command.op == "init":
                    raise SlackletProgramError("The slacklet is already initialized")
                data = program.apply(state.data, command.op, command.params)
            rendered = program.render(data)
            return ProgramState(
                name=name,
                data=data,
                text=rendered.text,
                rendered=[block.model_dump() for block in rendered.blocks],
            )

        async def start(subject: str, cfg: RunConfig) -> tuple[str, ProgramCommand]:  # noqa: ARG001
            raise SlackletRefused("Agent-defined slacklets start with create_slacklet")

        return SlackletKind(
            name=DYNAMIC_KIND,
            state=ProgramState,
            commands=PROGRAM_COMMAND,
            apply=apply,
            start=start,
        )


DYNAMIC_KIND = "dynamic"


class ProgramState(SlackletState):
    """An agent-defined slacklet's state, and the message its program rendered for it."""

    name: str
    data: JsonValue
    text: str
    rendered: list[JsonObject]

    def describe(self) -> str:
        return f"the agent-defined slacklet “{self.name}”"

    def blocks(self, *, seq: int, op: str) -> list[Block]:
        rendered = _RENDERED.validate_python({"text": self.text, "blocks": self.rendered})
        return [
            *(_block(block) for block in rendered.blocks),
            context(f"{self.name} · state after #{seq} {op}"),
        ]

    def fallback_text(self) -> str:
        return self.text


def _text(text: _Text) -> TextObject:
    return mrkdwn(text.text) if text.type == "mrkdwn" else plain_text(text.text, emoji=True)


def _block(block: _Section | _Context | _Divider) -> Block:
    if isinstance(block, _Section):
        built: SectionBlock = {"type": "section", "text": _text(block.text)}
        return built
    if isinstance(block, _Context):
        listed: ContextBlock = {"type": "context", "elements": [_text(t) for t in block.elements]}
        return listed
    return divider()


class ProgramCommand(SlackletCommand):
    """A command the agent's program interprets: ``init`` first, then any op it accepts."""

    op: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    params: JsonObject = {}
    note: str = Field(default="", max_length=200)

    def summary(self) -> str:
        return self.note or (
            f"{self.op} {json.dumps(self.params, sort_keys=True)}" if self.params else self.op
        )


PROGRAM_COMMAND = TypeAdapter(ProgramCommand)
