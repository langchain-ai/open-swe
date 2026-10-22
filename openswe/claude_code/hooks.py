"""Mirroring a Claude Code session into a read-only thread, one hook event at a time.

Claude Code posts each hook event to ``POST /dashboard/api/claude-code/hooks``.
Every event is appended to the thread's transcript log, which the dashboard
streams, so the thread follows the session as it runs. Hooks only see what
Claude Code hands them: each prompt, each tool call with its input and result,
and every message Claude writes, streamed as Claude Code displays it. Thinking
never reaches a hook.
A pull request the session binds in the Claude app, reads back as its own, or
opens with ``gh pr create`` is recorded on the thread and linked to it.

A session's thread id is derived from its owner and Claude Code's session id,
so nobody else's hooks can write into it however they name their session.
"""

import json
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    Tag,
    TypeAdapter,
    ValidationError,
)
from sqlalchemy import text

from openswe.claude_code.transcript import MAX_TITLE_CHARS
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.database import postgres
from openswe.github.http import GitHubClient, GitHubError
from openswe.github.pull_requests import PullRequest
from openswe.slack.client import parse_github_pr_url
from openswe.threads.creation import create_thread
from openswe.threads.summary import (
    CLAUDE_CODE_SOURCE,
    TRANSCRIPT_VERSION,
    upsert_pull_request_record,
)
from openswe.transcript.engine import Command, append
from openswe.transcript.events import (
    JsonObject,
    MessageAppended,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRequested,
    TurnStarted,
)
from openswe.transcript.mirror import mirror_thread_metadata
from openswe.transcript.turns import steer_target
from openswe.users import User
from openswe.users.authorization import is_authorized_github_login
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.utils.thread_participants import participant_metadata
from openswe.utils.time import now_ms

logger = logging.getLogger(__name__)

SESSION_ID_KEY = "claude_code_session_id"
# On an uploaded session's thread: the thread that already mirrors the same session.
MIRROR_THREAD_KEY = "claude_code_mirror_thread_id"
CWD_KEY = "claude_code_cwd"
_THREAD_NAMESPACE = uuid.UUID("5b0f3a52-7c1e-4d55-9a0e-c1a7de5c0de5")
_PASTED_CONTENT = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content>", re.DOTALL)
_UNTITLED = "Claude Code session"
_STOP_FAILED = "Claude Code stopped on an API error."
_PULL_REQUEST_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")


class _Hook(BaseModel):
    """What every hook event carries. Claude Code adds fields between releases; they are ignored."""

    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(min_length=1, max_length=200)
    cwd: str | None = None
    # Set on events from inside a subagent, whose steps the main session only sees as its tool call.
    agent_id: str | None = None


class PromptSubmitted(_Hook):
    hook_event_name: Literal["UserPromptSubmit"]
    prompt: str
    session_title: str | None = None


class _ToolHook(_Hook):
    tool_name: str
    tool_use_id: str = Field(min_length=1)
    tool_input: JsonValue = None

    @property
    def input(self) -> JsonObject:
        return self.tool_input if isinstance(self.tool_input, dict) else {"input": self.tool_input}


class ToolCalled(_ToolHook):
    hook_event_name: Literal["PreToolUse"]


class ToolReturned(_ToolHook):
    hook_event_name: Literal["PostToolUse"]
    tool_response: JsonValue = None

    @property
    def output(self) -> str | None:
        return _tool_output(self.tool_response)

    def pull_request_url(self) -> str | None:
        """The pull request this call tied the session to, if it did."""
        name = self.tool_name
        if name.startswith("mcp__") and name.endswith("__bind_pr"):
            url = self.input.get("url")
            found = url if isinstance(url, str) else ""
        elif name.startswith("mcp__") and name.endswith("_pr__get_status"):
            # Another session's PR is not this one's.
            if self.input.get("session_id") not in {None, "self"}:
                return None
            found = self.output or ""
        elif name == "Bash" and "gh pr create" in str(self.input.get("command") or ""):
            found = self.output or ""
        else:
            return None
        urls = _PULL_REQUEST_URL.findall(found)
        return urls[-1] if urls else None


class ToolFailed(_ToolHook):
    hook_event_name: Literal["PostToolUseFailure"]
    error: str = ""


class MessageShown(_Hook):
    """Lines of a message as Claude Code displays them; every batch of one message shares its id."""

    hook_event_name: Literal["MessageDisplay"]
    message_id: str = Field(min_length=1)
    index: int = Field(default=0, ge=0)
    final: bool = False
    delta: str = ""


class Stopped(_Hook):
    hook_event_name: Literal["Stop"]
    last_assistant_message: str | None = None


class StopFailed(_Hook):
    hook_event_name: Literal["StopFailure"]
    # The API error, not a reply.
    last_assistant_message: str | None = None


class SessionEnded(_Hook):
    hook_event_name: Literal["SessionEnd"]


class _PullRequestRef(BaseModel):
    ref: str


class _PullRequestPayload(BaseModel):
    title: str = ""
    state: str
    draft: bool = False
    merged_at: str | None = None
    html_url: str
    head: _PullRequestRef
    base: _PullRequestRef

    @property
    def lifecycle(self) -> str:
        if self.merged_at:
            return "merged"
        if self.state == "closed":
            return "closed"
        return "draft" if self.draft else "open"


class OtherHook(_Hook):
    """An event the mirror has nothing to show for."""

    hook_event_name: str


MIRRORED_EVENTS = frozenset(
    {
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "MessageDisplay",
        "Stop",
        "StopFailure",
        "SessionEnd",
    }
)


def _event_name(value: object) -> str:
    name = value.get("hook_event_name") if isinstance(value, dict) else None
    return name if isinstance(name, str) and name in MIRRORED_EVENTS else "other"


type HookEvent = Annotated[
    Annotated[PromptSubmitted, Tag("UserPromptSubmit")]
    | Annotated[ToolCalled, Tag("PreToolUse")]
    | Annotated[ToolReturned, Tag("PostToolUse")]
    | Annotated[ToolFailed, Tag("PostToolUseFailure")]
    | Annotated[MessageShown, Tag("MessageDisplay")]
    | Annotated[Stopped, Tag("Stop")]
    | Annotated[StopFailed, Tag("StopFailure")]
    | Annotated[SessionEnded, Tag("SessionEnd")]
    | Annotated[OtherHook, Tag("other")],
    Discriminator(_event_name),
]

HOOK_EVENT: TypeAdapter[HookEvent] = TypeAdapter(HookEvent)


def _title(prompt: str) -> str:
    text = _PASTED_CONTENT.sub(" ", prompt)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:MAX_TITLE_CHARS] or _UNTITLED


def _tool_output(response: JsonValue) -> str | None:
    """A tool's result as text: Bash's comes as output streams, an MCP tool's as content blocks."""
    if response is None:
        return None
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        streams = [response.get(name) for name in ("stdout", "stderr")]
        if any(isinstance(stream, str) for stream in streams):
            return "\n".join(stream for stream in streams if isinstance(stream, str) and stream)
    if isinstance(response, list):
        texts = [
            text
            for block in response
            if isinstance(block, dict) and isinstance(text := block.get("text"), str)
        ]
        if texts and len(texts) == len(response):
            return "\n".join(texts)
    return json.dumps(response, indent=2)


def mirror_thread_id(owner_id: UUID, session_id: str) -> str:
    """The id of the thread mirroring this person's session, whether or not its hooks ever ran."""
    return str(uuid.uuid5(_THREAD_NAMESPACE, f"{owner_id}:{session_id}"))


async def mirror_of(owner_id: UUID, session_id: str) -> str | None:
    """The thread mirroring this person's session, or ``None`` when it was never mirrored."""
    if not postgres.configured():
        return None
    thread_id = mirror_thread_id(owner_id, session_id)
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text("SELECT thread_id FROM thread WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        return result.scalar_one_or_none()


class SessionMirror:
    """One person's Claude Code session, as the read-only thread that mirrors it."""

    def __init__(self, owner: User, session_id: str) -> None:
        self.owner = owner
        self.session_id = session_id
        self.thread_id = mirror_thread_id(owner.id, session_id)
        # Turns carry a run id; this one only ever names the session.
        self._run_id = f"claude-code:{session_id}"

    async def record(self, event: HookEvent) -> None:
        if isinstance(event, OtherHook) or event.agent_id is not None:
            return
        title = await self._title()
        if title is None:
            if not isinstance(
                event, PromptSubmitted | ToolCalled | ToolReturned | ToolFailed | MessageShown
            ):
                return
            await self._create(event)
        match event:
            case PromptSubmitted():
                await self._prompt(event, title)
            case ToolCalled():
                turn_id, commands = await self._turn()
                await self._append([*commands, self._tool_started(turn_id, event)])
            case ToolReturned() | ToolFailed():
                await self._tool_ended(event)
            case MessageShown():
                await self._message_shown(event)
            case Stopped():
                await self._stopped(event)
            case StopFailed():
                await self._end_turn(
                    lambda turn_id: TurnFailed(
                        turn_id=turn_id, error=event.last_assistant_message or _STOP_FAILED
                    ),
                    "failed",
                )
            case SessionEnded():
                await self._end_turn(
                    lambda turn_id: TurnInterrupted(turn_id=turn_id), "interrupted"
                )

    async def _title(self) -> str | None:
        """The mirrored thread's title, or ``None`` before its first event."""
        async with postgres.read_only_transaction() as conn:
            result = await conn.execute(
                text("SELECT title FROM thread WHERE thread_id = :thread_id"),
                {"thread_id": self.thread_id},
            )
            return result.scalar_one_or_none()

    async def _create(self, event: _Hook) -> None:
        login = self.owner.github_login
        if not await is_authorized_github_login(login):
            raise PermissionError(
                "your GitHub account is not authorized for this Open SWE instance"
            )
        title = (
            (event.session_title or _title(event.prompt))
            if isinstance(event, PromptSubmitted)
            else _UNTITLED
        )
        created_ms = now_ms()
        metadata: dict[str, object] = {
            "source": CLAUDE_CODE_SOURCE,
            "origin": CLAUDE_CODE_SOURCE,
            "owner_type": "user",
            "owner_login": login,
            # A local session can hold anything on its machine, so only its owner sees it.
            "visibility": "private",
            "thread_category": "interactive",
            "trigger_kind": "user",
            **await participant_metadata({}, login=login, email=self.owner.email or None),
            "title": title,
            "transcript": TRANSCRIPT_VERSION,
            SESSION_ID_KEY: self.session_id,
            "created_at_ms": created_ms,
            "updated_at_ms": created_ms,
        }
        if event.cwd:
            metadata[CWD_KEY] = event.cwd
        client = langgraph_client()
        await create_thread(
            client, self.thread_id, title=title, metadata=metadata, if_exists="do_nothing"
        )
        thread = await client.threads.get(self.thread_id)
        await self._append(
            [
                Command(
                    command_id=f"thread:{self.thread_id}:created",
                    event=ThreadCreated(
                        title=title,
                        source=CLAUDE_CODE_SOURCE,
                        owner_login=login,
                        visibility="private",
                        metadata=thread_metadata(thread),
                    ),
                    actor_kind="user",
                )
            ]
        )
        logger.info(
            "Started mirroring a Claude Code session",
            extra={"thread_id": self.thread_id, "github_login": login},
        )

    async def _prompt(self, event: PromptSubmitted, title: str | None) -> None:
        commands: list[Command] = []
        # Claude Code fires no Stop when the person interrupts a turn; their next prompt ends it.
        if (open_turn := await steer_target(self.thread_id)) is not None:
            commands.append(self._ended(TurnInterrupted(turn_id=open_turn.turn_id), "interrupted"))
        turn_id = uuid.uuid7()
        commands += [
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id=f"human-{turn_id}",
                    text=event.prompt,
                    sender=MessageSender(login=self.owner.github_login, kind=CLAUDE_CODE_SOURCE),
                ),
                actor_kind="user",
                turn_id=turn_id,
            ),
            self._started(turn_id),
        ]
        await self._append(commands)
        renamed = event.session_title if event.session_title not in {None, title} else None
        await self._touch(title=renamed)

    async def _tool_ended(self, event: ToolReturned | ToolFailed) -> None:
        turn_id, commands = await self._turn()
        failed = isinstance(event, ToolFailed)
        output = event.error if isinstance(event, ToolFailed) else event.output
        await self._append(
            [
                *commands,
                # Deduplicated when PreToolUse already recorded it.
                self._tool_started(turn_id, event),
                Command(
                    command_id=f"tool:{event.tool_use_id}:completed",
                    event=ToolCompleted(
                        turn_id=turn_id,
                        tool_call_id=event.tool_use_id,
                        status="error" if failed else "completed",
                    ),
                    actor_kind="agent",
                    turn_id=turn_id,
                    tool_output=output or None,
                ),
            ]
        )
        if isinstance(event, ToolReturned) and (url := event.pull_request_url()) is not None:
            await self._link_pull_request(url)

    async def _message_shown(self, event: MessageShown) -> None:
        turn_id, commands = await self._turn()
        message_id = f"ai-{event.message_id}"
        if event.delta:
            commands.append(
                Command(
                    command_id=f"message:{message_id}:{event.index}",
                    event=MessageAppended(turn_id=turn_id, message_id=message_id, text=event.delta),
                    actor_kind="agent",
                    turn_id=turn_id,
                )
            )
        await self._append(commands)
        # The fragments already show the message; completing it settles the canonical text.
        if event.final and (shown := await self._message_text(message_id)):
            await self._append([self._reply(turn_id, message_id, shown)])

    async def _stopped(self, event: Stopped) -> None:
        reply = (event.last_assistant_message or "").strip()
        if (open_turn := await steer_target(self.thread_id)) is None and not reply:
            return
        turn_id, commands = await self._turn()
        # With MessageDisplay hooked, the reply was already mirrored as it was shown.
        if reply and not await self._replied(turn_id, reply):
            commands.append(self._reply(turn_id, f"ai-{turn_id}", reply))
        commands.append(self._ended(TurnCompleted(turn_id=turn_id), "completed"))
        await self._append(commands)
        if open_turn is not None:
            await self._touch()

    async def _message_text(self, message_id: str) -> str:
        async with postgres.read_only_transaction() as conn:
            result = await conn.execute(
                text(
                    "SELECT text FROM thread_message "
                    "WHERE thread_id = :thread_id AND message_id = :message_id"
                ),
                {"thread_id": self.thread_id, "message_id": message_id},
            )
            return (result.scalar_one_or_none() or "").strip()

    async def _replied(self, turn_id: UUID, reply: str) -> bool:
        async with postgres.read_only_transaction() as conn:
            result = await conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM thread_message WHERE thread_id = :thread_id "
                    "AND turn_id = :turn_id AND role = 'ai' AND btrim(text) = :reply)"
                ),
                {"thread_id": self.thread_id, "turn_id": turn_id, "reply": reply},
            )
            return bool(result.scalar_one())

    def _reply(self, turn_id: UUID, message_id: str, reply: str) -> Command:
        return Command(
            command_id=f"message:{message_id}:completed",
            event=MessageCompleted(
                turn_id=turn_id,
                message_id=message_id,
                role="ai",
                text=reply,
                created_at=datetime.now(UTC),
            ),
            actor_kind="agent",
            turn_id=turn_id,
        )

    async def _end_turn(
        self,
        event: Callable[[UUID], TurnFailed | TurnInterrupted],
        outcome: Literal["failed", "interrupted"],
    ) -> None:
        open_turn = await steer_target(self.thread_id)
        if open_turn is not None:
            await self._append([self._ended(event(open_turn.turn_id), outcome)])

    async def _turn(self) -> tuple[UUID, list[Command]]:
        """The turn the session is in, starting one when the hooks joined it mid-turn."""
        open_turn = await steer_target(self.thread_id)
        if open_turn is not None:
            return open_turn.turn_id, []
        turn_id = uuid.uuid7()
        return turn_id, [self._started(turn_id)]

    def _started(self, turn_id: UUID) -> Command:
        return Command(
            command_id=f"turn:{turn_id}:started",
            event=TurnStarted(turn_id=turn_id, run_id=self._run_id),
            actor_kind="agent",
            turn_id=turn_id,
        )

    def _ended(self, event: TurnCompleted | TurnFailed | TurnInterrupted, outcome: str) -> Command:
        return Command(
            command_id=f"turn:{event.turn_id}:{outcome}",
            event=event,
            actor_kind="user" if outcome == "interrupted" else "agent",
            turn_id=event.turn_id,
        )

    def _tool_started(self, turn_id: UUID, event: _ToolHook) -> Command:
        return Command(
            command_id=f"tool:{event.tool_use_id}:started",
            event=ToolStarted(
                turn_id=turn_id,
                tool_call_id=event.tool_use_id,
                name=event.tool_name,
                input=event.input,
            ),
            actor_kind="agent",
            turn_id=turn_id,
        )

    async def _append(self, commands: list[Command]) -> None:
        await append(self.thread_id, commands)

    async def _link_pull_request(self, url: str) -> None:
        """Record the pull request on the thread and link the two, as the owner can see it.

        Best effort: a session whose PR cannot be read still mirrors.
        """
        ref = parse_github_pr_url(url)
        if ref is None:
            return
        login = self.owner.github_login
        full_name = f"{ref.owner}/{ref.repo}"
        try:
            await require_repo_access_for_user(login, full_name)
            async with GitHubClient.as_user(login) as github:
                payload = await github.repo(ref.owner, ref.repo).pull_request(ref.number).pull()
            pull = _PullRequestPayload.model_validate(payload)
        except HTTPException, GitHubError, ValidationError:
            logger.warning(
                "Could not read a mirrored Claude Code session's pull request",
                extra={"thread_id": self.thread_id, "pr_url": url},
                exc_info=True,
            )
            return
        client = langgraph_client()
        records = thread_metadata(await client.threads.get(self.thread_id)).get("pull_requests")
        pull_requests = upsert_pull_request_record(
            records,
            {
                "repo_full_name": full_name,
                "number": ref.number,
                "url": pull.html_url,
                "title": pull.title,
                "state": pull.lifecycle,
                "head_ref": pull.head.ref,
                "base_ref": pull.base.ref,
            },
        )
        await client.threads.update(
            thread_id=self.thread_id,
            metadata={
                "repo_owner": ref.owner,
                "repo_name": ref.repo,
                "branch_name": pull.head.ref,
                "base_branch": pull.base.ref,
                "pr_url": pull.html_url,
                "pr_number": ref.number,
                "pr_state": pull.lifecycle,
                "pr_title": pull.title,
                "pull_requests": pull_requests,
                "pr_urls": [record["url"] for record in pull_requests if record.get("url")],
                "updated_at_ms": now_ms(),
            },
        )
        await PullRequest(owner=ref.owner, repo=ref.repo, number=ref.number).link_thread(
            self.thread_id, source=CLAUDE_CODE_SOURCE
        )
        logger.info(
            "Linked a mirrored Claude Code session to its pull request",
            extra={"thread_id": self.thread_id, "pr_url": pull.html_url},
        )

    async def _touch(self, *, title: str | None = None) -> None:
        """Move the thread up the sidebar, renaming it when Claude Code renamed the session."""
        patch: dict[str, object] = {"updated_at_ms": now_ms()}
        if title:
            patch["title"] = title
        await langgraph_client().threads.update(thread_id=self.thread_id, metadata=patch)
        await mirror_thread_metadata(self.thread_id, patch)
