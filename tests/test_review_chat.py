from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from agent.dashboard import review_chat_api

# `agent.tools.__init__` rebinds these names to the tool *functions*, shadowing
# the submodules. Import the real modules so we can monkeypatch their globals.
list_review_findings = importlib.import_module("agent.tools.list_review_findings")
read_repo_file = importlib.import_module("agent.tools.read_repo_file")
search_repo_code = importlib.import_module("agent.tools.search_repo_code")


# --- chat thread list / delete / title ---------------------------------------


def test_derive_title_from_first_user_message() -> None:
    params = {
        "input": {"messages": [{"type": "human", "content": "  Why did we drop\nstructs?  "}]}
    }
    assert review_chat_api._derive_title(params) == "Why did we drop structs?"


def test_derive_title_defaults_when_no_message() -> None:
    assert review_chat_api._derive_title({"input": {"messages": []}}) == "New chat"


def test_derive_title_truncates() -> None:
    params = {"input": {"messages": [{"type": "human", "content": "x" * 200}]}}
    assert len(review_chat_api._derive_title(params)) == review_chat_api._TITLE_MAX_CHARS


@pytest.mark.asyncio
async def test_list_review_chat_threads_scopes_and_maps(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def search(**kwargs: Any) -> list[dict[str, Any]]:
        captured["metadata"] = kwargs.get("metadata")
        return [
            {
                "thread_id": "c1",
                "updated_at": "2026-06-15T00:00:00Z",
                "metadata": {"title": "Why structs?"},
            },
            {"thread_id": "c2", "metadata": {}},  # untitled -> default label
        ]

    client = SimpleNamespace(threads=SimpleNamespace(search=search))
    monkeypatch.setattr(review_chat_api, "langgraph_client", lambda: client)

    threads = await review_chat_api.list_review_chat_threads("acme", "repo", 7, "octocat")
    assert captured["metadata"] == {
        "kind": "review_chat",
        "github_login": "octocat",
        "repo_owner": "acme",
        "repo_name": "repo",
        "pr_number": 7,
    }
    assert threads[0] == {
        "thread_id": "c1",
        "title": "Why structs?",
        "updated_at": "2026-06-15T00:00:00Z",
    }
    assert threads[1]["title"] == "New chat"


@pytest.mark.asyncio
async def test_delete_review_chat_thread_checks_ownership(monkeypatch) -> None:
    deleted: list[str] = []

    async def get(thread_id: str) -> dict[str, Any]:
        return {
            "thread_id": thread_id,
            "metadata": {
                "kind": "review_chat",
                "github_login": "octocat",
                "repo_owner": "acme",
                "repo_name": "repo",
                "pr_number": 7,
            },
        }

    async def delete(thread_id: str) -> None:
        deleted.append(thread_id)

    client = SimpleNamespace(threads=SimpleNamespace(get=get, delete=delete))
    monkeypatch.setattr(review_chat_api, "langgraph_client", lambda: client)

    await review_chat_api.delete_review_chat_thread("acme", "repo", 7, "octocat", "c1")
    assert deleted == ["c1"]


@pytest.mark.asyncio
async def test_delete_review_chat_thread_rejects_other_user(monkeypatch) -> None:
    async def get(thread_id: str) -> dict[str, Any]:
        return {
            "thread_id": thread_id,
            "metadata": {"kind": "review_chat", "github_login": "hubot"},
        }

    async def delete(thread_id: str) -> None:
        raise AssertionError("should not delete another user's chat")

    client = SimpleNamespace(threads=SimpleNamespace(get=get, delete=delete))
    monkeypatch.setattr(review_chat_api, "langgraph_client", lambda: client)

    with pytest.raises(Exception):  # noqa: B017,PT011 - HTTPException(404)
        await review_chat_api.delete_review_chat_thread("acme", "repo", 7, "octocat", "c1")


def _patch_thread_metadata(monkeypatch, metadata: dict[str, Any] | None) -> None:
    async def get(thread_id: str) -> dict[str, Any]:
        if metadata is None:
            raise RuntimeError("not found")
        return {"thread_id": thread_id, "metadata": metadata}

    client = SimpleNamespace(threads=SimpleNamespace(get=get))
    monkeypatch.setattr(review_chat_api, "langgraph_client", lambda: client)


@pytest.mark.asyncio
async def test_assert_chat_thread_access_allows_owner(monkeypatch) -> None:
    _patch_thread_metadata(
        monkeypatch,
        {
            "kind": "review_chat",
            "github_login": "octocat",
            "repo_owner": "acme",
            "repo_name": "repo",
            "pr_number": 7,
        },
    )
    meta = await review_chat_api.assert_chat_thread_access("ct-1", "acme", "repo", 7, "octocat")
    assert meta is not None


@pytest.mark.asyncio
async def test_assert_chat_thread_access_missing_thread_returns_none(monkeypatch) -> None:
    _patch_thread_metadata(monkeypatch, None)
    assert (
        await review_chat_api.assert_chat_thread_access("ct-1", "acme", "repo", 7, "octocat")
        is None
    )


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


def test_list_review_findings_compacts_and_filters(monkeypatch) -> None:
    monkeypatch.setattr(
        list_review_findings,
        "get_config",
        lambda: {"configurable": {"reviewer_thread_id": "rt-1"}},
    )

    async def fake_list(thread_id: str) -> list[dict[str, Any]]:
        assert thread_id == "rt-1"
        return [
            {
                "id": "f1",
                "title": "Open one",
                "status": "open",
                "severity": "high",
                "github_review_comment_id": 999,
            },
            {"id": "f2", "title": "Closed one", "status": "resolved", "severity": "low"},
        ]

    monkeypatch.setattr(list_review_findings, "list_findings_async", fake_list)

    result = list_review_findings.list_review_findings(status_filter="open")
    assert result["count"] == 1
    finding = result["findings"][0]
    assert finding["id"] == "f1"
    # compact view drops GitHub plumbing fields
    assert "github_review_comment_id" not in finding


def test_list_review_findings_requires_reviewer_thread(monkeypatch) -> None:
    monkeypatch.setattr(list_review_findings, "get_config", lambda: {"configurable": {}})
    result = list_review_findings.list_review_findings()
    assert result["count"] == 0
    assert "reviewer thread" in result["error"]


def test_read_repo_file_decodes_file(monkeypatch) -> None:
    import base64

    monkeypatch.setattr(
        read_repo_file,
        "get_config",
        lambda: {
            "configurable": {
                "chat_repo_owner": "acme",
                "chat_repo_name": "repo",
                "chat_github_token": "tok",
                "chat_head_sha": "deadbeef",
            }
        },
    )

    captured: dict[str, Any] = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"type": "file", "content": base64.b64encode(b"hello\nworld").decode()},
        )

    monkeypatch.setattr(read_repo_file.requests, "get", fake_get)

    result = read_repo_file.read_repo_file("src/app.py")
    assert result["success"] is True
    assert result["content"] == "hello\nworld"
    assert result["ref"] == "deadbeef"  # defaults to head sha
    assert captured["params"] == {"ref": "deadbeef"}


def test_read_repo_file_lists_directory(monkeypatch) -> None:
    monkeypatch.setattr(
        read_repo_file,
        "get_config",
        lambda: {
            "configurable": {
                "chat_repo_owner": "acme",
                "chat_repo_name": "repo",
                "chat_github_token": "tok",
            }
        },
    )

    def fake_get(url, headers=None, params=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: [
                {"name": "a.py", "type": "file", "path": "src/a.py"},
                {"name": "sub", "type": "dir", "path": "src/sub"},
            ],
        )

    monkeypatch.setattr(read_repo_file.requests, "get", fake_get)
    result = read_repo_file.read_repo_file("src")
    assert result["success"] is True
    assert {e["name"] for e in result["entries"]} == {"a.py", "sub"}


def test_read_repo_file_missing_context(monkeypatch) -> None:
    monkeypatch.setattr(read_repo_file, "get_config", lambda: {"configurable": {}})
    result = read_repo_file.read_repo_file("src/app.py")
    assert result["success"] is False


def test_search_repo_code_scopes_to_repo(monkeypatch) -> None:
    monkeypatch.setattr(
        search_repo_code,
        "get_config",
        lambda: {
            "configurable": {
                "chat_repo_owner": "acme",
                "chat_repo_name": "repo",
                "chat_github_token": "tok",
            }
        },
    )
    captured: dict[str, Any] = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "total_count": 1,
                "items": [{"path": "src/a.py", "text_matches": [{"fragment": "def foo()"}]}],
            },
        )

    monkeypatch.setattr(search_repo_code.requests, "get", fake_get)
    result = search_repo_code.search_repo_code("foo")
    assert result["success"] is True
    assert "repo:acme/repo" in captured["params"]["q"]
    assert result["results"][0]["path"] == "src/a.py"


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


def _patch_enrich_deps(monkeypatch, *, metadata: dict[str, Any] | None) -> dict[str, Any]:
    client, captured = _client_for_enrich(metadata)
    monkeypatch.setattr(review_chat_api, "langgraph_client", lambda: client)

    async def fake_get_review(owner, repo, pr_number):
        return _fake_review()

    async def fake_diff(*, owner, repo, pr_number, token):
        return "diff --git a/x b/x\n+added\n"

    async def fake_token(repositories=None):
        return "app-token"

    monkeypatch.setattr(review_chat_api, "get_review", fake_get_review)
    monkeypatch.setattr(review_chat_api, "fetch_pr_diff", fake_diff)
    monkeypatch.setattr(review_chat_api, "get_github_app_installation_token", fake_token)
    return captured


@pytest.mark.asyncio
async def test_enrich_chat_command_seeds_context_on_create(monkeypatch) -> None:
    _patch_enrich_deps(monkeypatch, metadata=None)
    command = {"method": "run.start", "params": {"input": {"messages": []}}}

    enriched = await review_chat_api._enrich_chat_command(
        command, owner="acme", repo="repo", pr_number=7, login="octocat", thread_id="ct-1"
    )

    params = enriched["params"]
    assert params["assistant_id"] == "chat"
    configurable = params["config"]["configurable"]
    assert configurable["chat_repo_owner"] == "acme"
    assert configurable["chat_repo_name"] == "repo"
    assert configurable["chat_pr_number"] == 7
    assert configurable["chat_head_sha"] == "abc123def456"
    assert configurable["reviewer_thread_id"] == review_chat_api.reviewer_thread_id(
        "acme", "repo", 7
    )
    files = params["input"]["files"]
    assert set(files) == {"/pr/overview.md", "/pr/diff.patch", "/pr/findings.md"}


@pytest.mark.asyncio
async def test_enrich_chat_command_reuses_context_when_head_unchanged(monkeypatch) -> None:
    # Stored head matches the current review head -> no reseed.
    captured = _patch_enrich_deps(
        monkeypatch, metadata={"kind": "review_chat", "chat_head_sha": "abc123def456"}
    )
    command = {"method": "run.start", "params": {"input": {"messages": []}}}

    enriched = await review_chat_api._enrich_chat_command(
        command, owner="acme", repo="repo", pr_number=7, login="octocat", thread_id="ct-1"
    )

    params = enriched["params"]
    # No re-seeding of files when the head hasn't moved.
    assert "files" not in params["input"]
    assert params["config"]["configurable"]["chat_head_sha"] == "abc123def456"
    assert captured["created"] is False
    assert captured["updated"] == []


@pytest.mark.asyncio
async def test_enrich_chat_command_reseeds_on_head_change(monkeypatch) -> None:
    # Stored head is stale relative to the current review head -> reseed.
    captured = _patch_enrich_deps(
        monkeypatch, metadata={"kind": "review_chat", "chat_head_sha": "old-stale-sha"}
    )
    command = {"method": "run.start", "params": {"input": {"messages": []}}}

    enriched = await review_chat_api._enrich_chat_command(
        command, owner="acme", repo="repo", pr_number=7, login="octocat", thread_id="ct-1"
    )

    params = enriched["params"]
    files = params["input"]["files"]
    assert set(files) == {"/pr/overview.md", "/pr/diff.patch", "/pr/findings.md"}
    assert params["config"]["configurable"]["chat_head_sha"] == "abc123def456"
    assert {"chat_head_sha": "abc123def456"} in captured["updated"]


@pytest.mark.asyncio
async def test_enrich_chat_command_ignores_non_run_start(monkeypatch) -> None:
    _patch_enrich_deps(monkeypatch, metadata=None)
    command = {"method": "something.else", "params": {}}
    enriched = await review_chat_api._enrich_chat_command(
        command, owner="acme", repo="repo", pr_number=7, login="octocat", thread_id="ct-1"
    )
    assert enriched == command
    assert "assistant_id" not in enriched["params"]


@pytest.mark.asyncio
async def test_proxy_state_normalizes_missing_thread(monkeypatch) -> None:
    async def fake_passthrough(method, thread_id, suffix, body, content_type):
        return 404, b"not found", "text/plain"

    async def no_thread(thread_id: str) -> None:
        return None

    monkeypatch.setattr(review_chat_api, "_proxy_passthrough", fake_passthrough)
    monkeypatch.setattr(review_chat_api, "_get_chat_thread_metadata", no_thread)
    status, content, media_type = await review_chat_api.proxy_review_chat_state(
        "acme", "repo", 7, "octocat", "ct-1"
    )
    assert status == 200
    assert b'"next": []' in content


@pytest.mark.asyncio
async def test_proxy_history_normalizes_missing_thread(monkeypatch) -> None:
    async def fake_passthrough(method, thread_id, suffix, body, content_type):
        return 404, b"not found", "text/plain"

    async def no_thread(thread_id: str) -> None:
        return None

    monkeypatch.setattr(review_chat_api, "_proxy_passthrough", fake_passthrough)
    monkeypatch.setattr(review_chat_api, "_get_chat_thread_metadata", no_thread)
    status, content, _ = await review_chat_api.proxy_review_chat_history(
        "acme", "repo", 7, "octocat", "ct-1", b"{}"
    )
    assert status == 200
    assert content == b"[]"


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


def test_get_chat_agent_returns_trivial_when_not_for_execution() -> None:
    from agent.chat import get_chat_agent

    graph = asyncio.run(get_chat_agent({"configurable": {"thread_id": None}}))
    assert graph is not None
