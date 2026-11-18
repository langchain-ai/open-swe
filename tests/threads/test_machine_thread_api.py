"""Machine principals on the one command endpoint the dashboard also uses."""

from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid7

import pytest
from fastapi import HTTPException

from openswe.dashboard.langsmith_api_keys import LangSmithApiKey
from openswe.federation.github_oidc import GitHubActionsClaims
from openswe.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from openswe.tasks import activity
from openswe.tasks.routes import api_thread_workers
from openswe.threads import index, runs
from openswe.threads.principals import STARTED_BY_ID, Principal
from openswe.transcript.attachments import PendingAttachment
from openswe.transcript.cursor import TurnPageCursor, encode_turn_cursor
from openswe.transcript.engine import Command, append
from openswe.transcript.events import (
    MessageAttachment,
    MessageSender,
    ThreadCreated,
    ToolCompleted,
    ToolStarted,
    TurnRequested,
)
from openswe.transcript.routes import (
    _stream,
    api_get_thread_attachment,
    api_get_thread_tool_output,
    api_get_thread_transcript,
    api_get_thread_transcript_turns,
    api_stream_thread_transcript,
)
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate


class _FakeThreads:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    async def get(self, thread_id: str) -> dict[str, Any]:
        record = next(item for item in self.created if item["thread_id"] == thread_id)
        return {"thread_id": thread_id, "status": "idle", "metadata": record["metadata"]}


class _FakeRuns:
    async def list(self, thread_id: str, **kwargs: object) -> list[dict[str, object]]:
        return []


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()


def _command(prompt: str, **configurable: Any) -> dict[str, Any]:
    return {
        "id": 1,
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": prompt}]},
            "config": {"configurable": configurable},
        },
    }


@pytest.fixture
async def machine(monkeypatch: pytest.MonkeyPatch, registry_db: None) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(runs, "langgraph_client", lambda: client)

    async def fake_repo_access(full_name: str) -> str:
        return "workspace-app-token"

    monkeypatch.setattr(runs, "require_repo_access_for_workspace", fake_repo_access)
    await WORKSPACES.create(WorkspaceCreate(name="core", repos=["acme/api"]), "admin")
    await WORKSPACES.create(WorkspaceCreate(name="oss", repos=["acme/oss"]), "admin")
    return client


def _bot_caller(workspace: str = "core") -> Principal:
    key = LangSmithApiKey(
        kind="service",
        fingerprint="0123456789abcdef",
        organization_id="org",
        workspace_id="ws",
        audit_id="langsmith:key-1",
    )
    return Principal.of_bot(key, workspace)


def _workflow_caller(
    repository: str = "acme/api", workspace: str = "core", visibility: str = "private"
) -> Principal:
    return Principal.of_workflow(
        GitHubActionsClaims(
            sub=f"repo:{repository}:ref:refs/heads/main",
            repository=repository,
            repository_owner=repository.split("/", 1)[0],
            repository_visibility=visibility,
            workflow_ref=f"{repository}/.github/workflows/nightly.yml@refs/heads/main",
        ),
        workspace,
    )


async def test_the_bot_starts_a_system_thread_with_no_person_on_it(machine: _FakeClient) -> None:
    principal = _bot_caller()

    enriched = await runs._enrich_system_run_start_command(
        "thread-1",
        principal,
        _command("Upgrade the linter", thread_type="system", repo="acme/api"),
        metadata={},
        creating=True,
    )

    metadata = machine.threads.created[0]["metadata"]
    assert metadata["source"] == "api"
    assert metadata["owner_type"] == "system"
    assert metadata["visibility"] == "public"
    assert metadata["workspace"] == "core"
    assert metadata["created_by"] == "bot"
    assert metadata["started_by_name"] == "Robin bot"
    assert metadata["started_by_id"] == "bot:ws"
    assert (metadata["repo_owner"], metadata["repo_name"]) == ("acme", "api")
    assert "owner_login" not in metadata

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["source"] == "api"
    assert configurable["workspace"] == configurable["environment"] == "core"
    assert configurable["repo"] == {"owner": "acme", "name": "api"}
    assert configurable["invocation_id"]
    assert configurable["origin"] == "api"
    assert enriched["params"]["metadata"]["origin"] == "api"
    assert "github_login" not in configurable
    assert "user_email" not in configurable
    assert "admin_thread" not in configurable
    assert "agent_model_id" not in configurable


async def test_a_machine_run_uses_the_model_its_client_names(machine: _FakeClient) -> None:
    enriched = await runs._enrich_system_run_start_command(
        "thread-model",
        _bot_caller(),
        _command(
            "Upgrade the linter",
            thread_type="system",
            agent_model_id="anthropic:claude-opus-5-5",
            agent_effort="medium",
        ),
        metadata={},
        creating=True,
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["agent_model_id"] == "anthropic:claude-opus-5-5"
    assert configurable["agent_effort"] == "medium"
    assert configurable["model_selection"] == "explicit"


@pytest.mark.parametrize(
    "choice",
    [
        {"agent_model_id": "anthropic:claude-opus-5-5"},
        {"agent_model_id": "anthropic:claude-opus-5-5", "agent_effort": "turbo"},
        {"agent_model_id": "acme:unknown-model", "agent_effort": "medium"},
        {"agent_effort": "medium"},
    ],
)
async def test_a_machine_run_refuses_a_model_it_cannot_run(
    machine: _FakeClient, choice: dict[str, str]
) -> None:
    with pytest.raises(HTTPException) as refused:
        await runs._enrich_system_run_start_command(
            "thread-bad-model",
            _bot_caller(),
            _command("Upgrade the linter", thread_type="system", **choice),
            metadata={},
            creating=True,
        )

    assert refused.value.status_code == 422
    assert machine.threads.created == []


async def test_a_machine_run_keeps_the_message_id_its_client_minted(machine: _FakeClient) -> None:
    command = _command("Upgrade the linter", thread_type="system", repo="acme/api")
    command["params"]["input"]["messages"][-1]["id"] = "prompt-1"

    enriched = await runs._enrich_system_run_start_command(
        "thread-ids", _bot_caller(), command, metadata={}, creating=True
    )

    assert enriched["params"]["input"]["messages"][-1]["id"] == "prompt-1"


async def test_a_machine_run_drops_a_message_id_the_thread_already_holds(
    machine: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = _bot_caller()

    class _State:
        async def state(self, thread_id: str) -> dict[str, object]:
            return {"values": {"messages": [{"id": "prompt-1", "type": "human", "content": "x"}]}}

    monkeypatch.setattr(runs, "THREADS", _State())
    command = _command("Upgrade the linter", thread_type="system")
    command["params"]["input"]["messages"][-1]["id"] = "prompt-1"

    enriched = await runs._enrich_system_run_start_command(
        "thread-ids",
        principal,
        command,
        metadata={STARTED_BY_ID: principal.started_by_id, "repo_owner": "acme", "repo_name": "api"},
    )

    assert "id" not in enriched["params"]["input"]["messages"][-1]


async def test_a_workflow_defaults_to_its_own_repository(machine: _FakeClient) -> None:
    enriched = await runs._enrich_system_run_start_command(
        "thread-2",
        _workflow_caller(),
        _command("Fix the nightly", thread_type="system"),
        metadata={},
        creating=True,
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["repo"] == {"owner": "acme", "name": "api"}
    assert machine.threads.created[0]["metadata"]["started_by_id"] == "github_actions:acme/api"


async def test_a_repository_another_workspace_prefers_is_usable(machine: _FakeClient) -> None:
    principal = _bot_caller()

    await runs._enrich_system_run_start_command(
        "thread-3",
        principal,
        _command("Upgrade the linter", thread_type="system", repo="acme/oss"),
        metadata={},
        creating=True,
    )

    assert machine.threads.created[0]["metadata"]["repo_name"] == "oss"


@pytest.mark.parametrize("requested", ["workspace", "private"])
async def test_a_machine_cannot_start_a_person_thread(machine: _FakeClient, requested: str) -> None:
    principal = _bot_caller()

    with pytest.raises(HTTPException) as refused:
        await runs._enrich_system_run_start_command(
            "thread-4",
            principal,
            _command("Upgrade the linter", thread_type=requested),
            metadata={},
            creating=True,
        )

    assert refused.value.status_code == 403
    assert machine.threads.created == []


async def test_the_thread_kind_has_to_be_named(machine: _FakeClient) -> None:
    principal = _bot_caller()

    with pytest.raises(HTTPException) as refused:
        await runs._enrich_system_run_start_command(
            "thread-5",
            principal,
            _command("Upgrade the linter"),
            metadata={},
            creating=True,
        )

    assert refused.value.status_code == 422


def test_only_an_admin_may_ask_for_a_system_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    Principal.of_login("admin").authorize("system")
    Principal.of_login("intern").authorize("workspace")
    Principal.of_login("intern").authorize("private")

    with pytest.raises(HTTPException) as refused:
        Principal.of_login("intern").authorize("system")
    assert refused.value.status_code == 403


async def test_a_machine_reads_back_only_what_it_started(machine: _FakeClient) -> None:
    mine = _bot_caller()
    theirs = _workflow_caller()

    started = {"started_by_id": mine.started_by_id}
    mine.assert_can_read(started)
    with pytest.raises(HTTPException) as refused:
        theirs.assert_can_read(started)
    assert refused.value.status_code == 404


@pytest.mark.parametrize(
    ("visibility", "scope"),
    [("public", ["acme/api"]), ("", ["acme/api"]), ("private", None), ("internal", None)],
)
async def test_a_workflow_on_a_public_repository_records_a_single_repository_scope(
    machine: _FakeClient, visibility: str, scope: list[str] | None
) -> None:
    await runs._enrich_system_run_start_command(
        "thread-scope",
        _workflow_caller(visibility=visibility),
        _command("Fix the nightly", thread_type="system"),
        metadata={},
        creating=True,
    )

    assert machine.threads.created[0]["metadata"].get(GITHUB_TOKEN_REPOSITORIES_KEY) == scope


@dataclass(frozen=True)
class _Transcript:
    thread_id: str
    turn_cursor: str
    tool_call_id: str
    attachment_id: UUID


async def _transcript_started_by(principal: Principal) -> _Transcript:
    thread_id, turn_id, attachment_id = str(uuid7()), uuid7(), uuid7()
    metadata = {
        "source": "api",
        "owner_type": "system",
        "visibility": "public",
        STARTED_BY_ID: principal.started_by_id,
    }
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title="A thread", source="api", owner_login="", metadata=metadata
                ),
                actor_kind="agent",
            ),
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id="human-1",
                    text="Upgrade the linter",
                    sender=MessageSender(login="system", kind="system"),
                    attachments=[
                        MessageAttachment(mime_type="text/plain", attachment_id=attachment_id)
                    ],
                ),
                actor_kind="agent",
                turn_id=turn_id,
                attachments=(
                    PendingAttachment(
                        attachment_id=attachment_id,
                        message_id="human-1",
                        position=0,
                        mime_type="text/plain",
                        file_name="log.txt",
                        data=b"log",
                    ),
                ),
            ),
            Command(
                command_id=f"tool:{thread_id}:started",
                event=ToolStarted(turn_id=turn_id, tool_call_id="call-1", name="shell", input={}),
                actor_kind="agent",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"tool:{thread_id}:completed",
                event=ToolCompleted(
                    turn_id=turn_id,
                    tool_call_id="call-1",
                    status="completed",
                    output_preview="ok",
                    has_output=True,
                ),
                tool_output="ok",
                actor_kind="agent",
                turn_id=turn_id,
            ),
        ],
    )
    cursor = TurnPageCursor(
        thread_id=thread_id, before_requested_at=datetime.now(UTC), before_turn_id=uuid7()
    )
    return _Transcript(thread_id, encode_turn_cursor(cursor), "call-1", attachment_id)


type _TranscriptRead = Callable[[_Transcript, Principal], Awaitable[object]]


_TRANSCRIPT_READS: dict[str, _TranscriptRead] = {
    "snapshot": lambda t, p: api_get_thread_transcript(t.thread_id, principal=p),
    "turns": lambda t, p: api_get_thread_transcript_turns(
        t.thread_id, before=t.turn_cursor, principal=p
    ),
    "events": lambda t, p: api_stream_thread_transcript(t.thread_id, principal=p),
    "tool output": lambda t, p: api_get_thread_tool_output(
        t.thread_id, t.tool_call_id, principal=p
    ),
    "attachment": lambda t, p: api_get_thread_attachment(t.thread_id, t.attachment_id, principal=p),
}


@pytest.mark.parametrize("read", _TRANSCRIPT_READS.values(), ids=_TRANSCRIPT_READS.keys())
async def test_a_machine_reads_the_transcript_of_only_the_thread_it_started(
    machine: _FakeClient, read: _TranscriptRead
) -> None:
    mine = _bot_caller()
    theirs = _workflow_caller()
    transcript = await _transcript_started_by(mine)

    await read(transcript, mine)
    with pytest.raises(HTTPException) as refused:
        await read(transcript, theirs)
    assert refused.value.status_code == 404


async def test_a_machine_reads_the_workers_of_only_the_thread_it_started(
    machine: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = _bot_caller()
    theirs = _workflow_caller()
    thread_id = str(uuid7())
    await index.create(
        thread_id, runtime="mda", metadata={"source": "api", STARTED_BY_ID: mine.started_by_id}
    )
    monkeypatch.setattr(activity, "langgraph_client", lambda: machine)

    assert (await api_thread_workers(thread_id, mine)).settled
    with pytest.raises(HTTPException) as refused:
        await api_thread_workers(thread_id, theirs)
    assert refused.value.status_code == 404


async def test_a_machine_stream_ends_on_a_thread_another_machine_started(
    machine: _FakeClient,
) -> None:
    mine = _bot_caller()
    theirs = _workflow_caller()
    transcript = await _transcript_started_by(mine)

    async with aclosing(_stream(transcript.thread_id, 0, theirs)) as stream:
        assert await anext(stream) == "event: revoked\ndata: {}\n\n"
