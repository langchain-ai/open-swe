import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.middleware.filesystem import FilesystemMiddleware
from fastapi import HTTPException

from agent.review import chat as review_chat_api
from agent.tools.errors import ToolError

# `agent.tools.__init__` rebinds these names to the tool *functions*, shadowing
# the submodules. Import the real modules so we can monkeypatch their globals.
list_review_findings = importlib.import_module("agent.tools.list_review_findings")
read_repo_file = importlib.import_module("agent.github.tools.read_repo_file")
search_repo_code = importlib.import_module("agent.github.tools.search_repo_code")
web_search = importlib.import_module("agent.tools.web_search")


# --- chat thread title -------------------------------------------------------


def _patch_thread_metadata(monkeypatch, metadata: dict[str, Any] | None) -> None:
    async def get(thread_id: str) -> dict[str, Any]:
        if metadata is None:
            raise RuntimeError("not found")
        return {"thread_id": thread_id, "metadata": metadata}

    client = SimpleNamespace(threads=SimpleNamespace(get=get))
    monkeypatch.setattr(review_chat_api, "langgraph_client", lambda: client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {  # another user's chat thread
            "kind": "review_chat",
            "github_login": "hubot",
            "repo_owner": "acme",
            "repo_name": "repo",
            "pr_number": 7,
        },
        {  # a reviewer (non-chat) thread with the same deterministic id space
            "kind": "reviewer",
            "github_login": "octocat",
            "repo_owner": "acme",
            "repo_name": "repo",
            "pr_number": 7,
        },
        {  # right user, wrong PR scope
            "kind": "review_chat",
            "github_login": "octocat",
            "repo_owner": "acme",
            "repo_name": "repo",
            "pr_number": 8,
        },
    ],
)
async def test_assert_chat_thread_access_rejects_unauthorized(monkeypatch, metadata) -> None:
    _patch_thread_metadata(monkeypatch, metadata)
    with pytest.raises(Exception):  # noqa: B017,PT011 - HTTPException(404)
        await review_chat_api.assert_chat_thread_access("ct-1", "acme", "repo", 7, "octocat")


# --- tools -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_tools_require_context_and_token(monkeypatch) -> None:
    monkeypatch.setattr("agent.run_config.get_config", lambda: {"configurable": {}})
    with pytest.raises(ToolError):
        await read_repo_file.read_repo_file("src/app.py")

    config = {"configurable": {"chat_repo_owner": "acme", "chat_repo_name": "repo"}}
    monkeypatch.setattr("agent.run_config.get_config", lambda: config)
    for tool, args in (
        (read_repo_file.read_repo_file, ("src/app.py",)),
        (search_repo_code.search_repo_code, ("foo",)),
    ):
        with pytest.raises(ToolError, match="GitHub credentials unavailable"):
            await tool(*args)


# --- proxy enrichment --------------------------------------------------------


def _fake_review() -> dict[str, Any]:
    return {
        "title": "Fix things",
        "number": 7,
        "full_name": "acme/repo",
        "author": "octocat",
        "head_ref": "feature",
        "base_ref": "main",
        "head_sha": "abc123def456",
        "findings": [
            {
                "id": "f1",
                "title": "Bug",
                "severity": "high",
                "confidence": "high",
                "status": "open",
                "file": "src/a.py",
                "start_line": 5,
                "description": "boom",
                "group": "bug",
            },
        ],
        "pr": {
            "state": "open",
            "body": "desc",
            "additions": 1,
            "deletions": 2,
            "changed_files": 1,
            "commits": 1,
            "head_sha": "abc123def456",
        },
    }


def _client_for_enrich(existing_metadata: dict[str, Any] | None) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {"created": False, "updated": []}

    async def get(thread_id: str) -> dict[str, Any]:
        if existing_metadata is None:
            raise RuntimeError("not found")
        return {"thread_id": thread_id, "metadata": existing_metadata}

    async def create(**kwargs: Any) -> None:
        captured["created"] = True

    async def update(**kwargs: Any) -> None:
        captured["updated"].append(kwargs.get("metadata"))

    client = SimpleNamespace(threads=SimpleNamespace(get=get, create=create, update=update))
    return client, captured


def _patch_enrich_deps(
    monkeypatch,
    *,
    metadata: dict[str, Any] | None,
    current_head: str = "abc123def456",
    last_reviewed: str = "",
) -> dict[str, Any]:
    client, captured = _client_for_enrich(metadata)
    monkeypatch.setattr(review_chat_api, "langgraph_client", lambda: client)

    async def fake_get_review(owner, repo, pr_number):
        return _fake_review()

    async def fake_diff(*, owner, repo, pr_number, token):
        return "diff --git a/x b/x\n+added\n"

    async def fake_token(repositories=None):
        return "app-token"

    async def fake_head(owner, repo, pr_number):
        captured["head_calls"] = captured.get("head_calls", 0) + 1
        return current_head

    monkeypatch.setattr(review_chat_api, "get_review", fake_get_review)
    monkeypatch.setattr(review_chat_api, "fetch_pr_diff", fake_diff)
    monkeypatch.setattr(review_chat_api, "get_github_app_installation_token", fake_token)
    monkeypatch.setattr(review_chat_api, "get_pr_head_sha", fake_head)

    async def fake_last_reviewed(owner, repo, pr_number):
        return last_reviewed

    monkeypatch.setattr(review_chat_api, "_last_reviewed_sha", fake_last_reviewed)
    return captured


@pytest.mark.asyncio
async def test_enrich_chat_command_reseeds_when_a_review_publishes_on_the_same_head(
    monkeypatch,
) -> None:
    # The chat started before any review; a review of the same head has since
    # published, so its findings must replace the seeded "no findings" file.
    captured = _patch_enrich_deps(
        monkeypatch,
        metadata={"kind": "review_chat", "chat_head_sha": "abc123def456"},
        last_reviewed="abc123def456",
    )
    command = {"method": "run.start", "params": {"input": {"messages": []}}}

    enriched = await review_chat_api._enrich_chat_command(
        command, owner="acme", repo="repo", pr_number=7, login="octocat", thread_id="ct-1"
    )

    assert "/pr/findings.md" in enriched["params"]["input"]["files"]
    assert {"chat_head_sha": "abc123def456", "chat_review_sha": "abc123def456"} in captured[
        "updated"
    ]


@pytest.mark.asyncio
async def test_enrich_chat_command_keeps_context_when_reseed_fails(monkeypatch) -> None:
    # Existing chat whose head moved, but loading the fresh context fails: the
    # command must keep answering from the last seeded context instead of erroring.
    captured = _patch_enrich_deps(
        monkeypatch,
        metadata={"kind": "review_chat", "chat_head_sha": "old-stale-sha"},
        current_head="new-head-sha",
    )

    async def failing_build(*args, **kwargs):
        raise HTTPException(404, "review not found")

    monkeypatch.setattr(review_chat_api, "_build_pr_context", failing_build)
    command = {"method": "run.start", "params": {"input": {"messages": []}}}

    enriched = await review_chat_api._enrich_chat_command(
        command,
        owner="acme",
        repo="repo",
        pr_number=7,
        login="octocat",
        thread_id="ct-1",
        thread_metadata={"kind": "review_chat", "chat_head_sha": "old-stale-sha"},
    )

    params = enriched["params"]
    assert "files" not in params["input"]  # no reseed
    assert params["config"]["configurable"]["chat_head_sha"] == "old-stale-sha"
    assert params["assistant_id"] == "chat"
    assert captured["updated"] == []  # head metadata not advanced on failure


@pytest.mark.asyncio
async def test_enrich_chat_command_surfaces_reseed_failure_on_create(monkeypatch) -> None:
    # A brand-new chat has no prior context to fall back to, so a seeding failure
    # must surface rather than silently produce an empty conversation.
    _patch_enrich_deps(monkeypatch, metadata=None)

    async def failing_build(*args, **kwargs):
        raise HTTPException(404, "review not found")

    monkeypatch.setattr(review_chat_api, "_build_pr_context", failing_build)
    command = {"method": "run.start", "params": {"input": {"messages": []}}}

    with pytest.raises(HTTPException):
        await review_chat_api._enrich_chat_command(
            command, owner="acme", repo="repo", pr_number=7, login="octocat", thread_id="ct-1"
        )


@pytest.mark.asyncio
async def test_proxy_state_rejects_foreign_thread(monkeypatch) -> None:
    async def other_owner(thread_id: str) -> dict[str, Any]:
        return {
            "kind": "review_chat",
            "github_login": "hubot",
            "repo_owner": "acme",
            "repo_name": "repo",
            "pr_number": 7,
        }

    async def fake_passthrough(*args, **kwargs):
        raise AssertionError("must not proxy a thread the caller doesn't own")

    monkeypatch.setattr(review_chat_api, "_get_chat_thread_metadata", other_owner)
    monkeypatch.setattr(review_chat_api, "_proxy_passthrough", fake_passthrough)
    with pytest.raises(Exception):  # noqa: B017,PT011 - HTTPException(404)
        await review_chat_api.proxy_review_chat_state("acme", "repo", 7, "octocat", "ct-1")


# --- graph factory guard -----------------------------------------------------


def test_chat_excludes_mutating_filesystem_tools() -> None:
    from agent.chat import _EXCLUDED_TOOLS

    assert {"write_file", "edit_file", "delete", "execute"} <= _EXCLUDED_TOOLS


def test_chat_general_purpose_subagent_is_read_only() -> None:
    from agent.chat import _chat_general_purpose_subagent

    spec = _chat_general_purpose_subagent()

    assert spec["name"] == "general-purpose"
    fs_middleware = [m for m in spec.get("middleware", []) if isinstance(m, FilesystemMiddleware)]
    assert len(fs_middleware) == 1
    enabled = fs_middleware[0]._enabled_tools
    assert enabled is not None
    assert {"write_file", "edit_file", "delete", "execute"}.isdisjoint(enabled)
    assert {"read_file", "ls", "glob", "grep"} <= enabled
