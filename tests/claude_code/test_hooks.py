"""Claude Code hook events, as the read-only thread that mirrors their session shows them."""

from collections.abc import Mapping

import httpx
import pytest
from fastapi import FastAPI

from openswe.claude_code import hooks
from openswe.claude_code.hooks import HOOK_EVENT, SessionMirror, ToolReturned
from openswe.claude_code.routes import router
from openswe.claude_code.sync_tokens import ClaudeCodeSyncToken
from openswe.transcript.snapshot import load_snapshot
from openswe.users import User


class _Threads:
    """The LangGraph thread API, as far as creating and touching a mirrored thread needs it."""

    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, object]] = {}

    async def create(
        self, *, thread_id: str, if_exists: str, metadata: Mapping[str, object]
    ) -> dict[str, object]:
        self.metadata.setdefault(thread_id, dict(metadata))
        return await self.get(thread_id)

    async def get(self, thread_id: str) -> dict[str, object]:
        return {"thread_id": thread_id, "metadata": self.metadata[thread_id]}

    async def update(self, *, thread_id: str, metadata: Mapping[str, object]) -> None:
        self.metadata[thread_id].update(metadata)


class _Client:
    def __init__(self) -> None:
        self.threads = _Threads()


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> _Threads:
    client = _Client()
    monkeypatch.setattr(hooks, "langgraph_client", lambda: client)
    return client.threads


def _event(name: str, **fields: object) -> dict[str, object]:
    return {"hook_event_name": name, "session_id": "session-1", "cwd": "/repo", **fields}


async def _record(owner: User, *events: dict[str, object]) -> SessionMirror:
    mirror = SessionMirror(owner, "session-1")
    for event in events:
        await mirror.record(HOOK_EVENT.validate_python(event))
    return mirror


async def test_a_session_mirrors_as_turns_and_an_interrupted_one_is_closed(
    registry_db: None, threads: _Threads
) -> None:
    owner = await User.sign_in("github", "1", login="test-user")
    mirror = await _record(
        owner,
        _event("UserPromptSubmit", prompt="Fix the flaky test\nmore detail"),
        _event("PreToolUse", tool_name="Bash", tool_use_id="t1", tool_input={"command": "ls"}),
        # A follow-up with no Stop before it: the person interrupted the first turn.
        _event("UserPromptSubmit", prompt="Actually, run the tests"),
        _event("MessageDisplay", message_id="m1", index=0, final=True, delta="Running them.\n"),
        # PostToolUse alone still records the call.
        _event(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="t2",
            tool_input={"command": "pytest"},
            tool_response={"stdout": "1 passed", "stderr": ""},
        ),
        _event("MessageDisplay", message_id="m2", index=0, delta="The tests\n"),
        _event("MessageDisplay", message_id="m2", index=1, final=True, delta="pass."),
        # The reply was mirrored as it was shown, so Stop does not add it again.
        _event("Stop", last_assistant_message="The tests\npass."),
    )

    snapshot = await load_snapshot(mirror.thread_id)
    assert snapshot is not None
    assert snapshot.thread.title == "Fix the flaky test"
    assert snapshot.thread.status == "idle"
    assert [turn.state for turn in snapshot.turns] == ["interrupted", "completed"]
    assert [(m.role, m.text) for m in snapshot.messages] == [
        ("human", "Fix the flaky test\nmore detail"),
        ("human", "Actually, run the tests"),
        ("ai", "Running them."),
        ("ai", "The tests\npass."),
    ]
    calls = {call.tool_call_id: call for call in snapshot.tool_calls}
    assert calls["t1"].status == "in_progress"
    assert (calls["t2"].status, calls["t2"].output_preview) == ("completed", "1 passed")
    assert threads.metadata[mirror.thread_id]["source"] == "claude_code"
    assert threads.metadata[mirror.thread_id]["visibility"] == "private"


async def test_only_the_owners_token_writes_their_session(
    registry_db: None, threads: _Threads
) -> None:
    owner = await User.sign_in("github", "1", login="test-user")
    other = await User.sign_in("github", "2", login="trusted-user")
    _, secret = await ClaudeCodeSyncToken.create(user_id=owner.id, name="laptop")
    revoked, revoked_secret = await ClaudeCodeSyncToken.create(user_id=owner.id, name="old")
    await ClaudeCodeSyncToken.revoke(user_id=owner.id, token_id=revoked.id)
    app = FastAPI()
    app.include_router(router)
    prompt = _event("UserPromptSubmit", prompt="hello")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for presented in ("occ_unknown", revoked_secret):
            refused = await client.post(
                "/claude-code/hooks", json=prompt, headers={"Authorization": f"Bearer {presented}"}
            )
            assert refused.status_code == 401
        accepted = await client.post(
            "/claude-code/hooks", json=prompt, headers={"Authorization": f"Bearer {secret}"}
        )

    assert (accepted.status_code, accepted.content) == (204, b"")
    assert await load_snapshot(SessionMirror(owner, "session-1").thread_id) is not None
    # The same session id under someone else names a different thread.
    assert await load_snapshot(SessionMirror(other, "session-1").thread_id) is None


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "tool_response", "expected"),
    [
        (
            "mcp__ccd_pr__bind_pr",
            {"url": "https://github.com/acme/app/pull/7"},
            "Bound.",
            "https://github.com/acme/app/pull/7",
        ),
        (
            "Bash",
            {"command": "gh pr create --fill"},
            {"stdout": "Creating pull request\nhttps://github.com/acme/app/pull/8\n"},
            "https://github.com/acme/app/pull/8",
        ),
        (
            "mcp__ccd_pr__get_status",
            {"session_id": "local_other"},
            '{"url": "https://github.com/acme/app/pull/9"}',
            None,
        ),
        (
            "Bash",
            {"command": "gh pr view 9"},
            {"stdout": "https://github.com/acme/app/pull/9"},
            None,
        ),
    ],
)
def test_the_pull_request_a_tool_call_ties_the_session_to(
    tool_name: str, tool_input: object, tool_response: object, expected: str | None
) -> None:
    event = HOOK_EVENT.validate_python(
        _event(
            "PostToolUse",
            tool_name=tool_name,
            tool_use_id="t1",
            tool_input=tool_input,
            tool_response=tool_response,
        )
    )
    assert isinstance(event, ToolReturned)
    assert event.pull_request_url() == expected
