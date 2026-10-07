import importlib

import pytest
from deepagents.middleware.filesystem import FilesystemMiddleware
from fastapi import HTTPException

from openswe.review import chat as review_chat_api

# `openswe.tools.__init__` rebinds these names to the tool *functions*, shadowing
# the submodules. Import the real modules so we can monkeypatch their globals.
list_review_findings = importlib.import_module("openswe.tools.list_review_findings")
read_repo_file = importlib.import_module("openswe.github.tools.read_repo_file")
search_repo_code = importlib.import_module("openswe.github.tools.search_repo_code")
web_search = importlib.import_module("openswe.tools.web_search")


# --- tools -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_tools_require_context_and_token(monkeypatch) -> None:
    monkeypatch.setattr("openswe.run_config.get_config", lambda: {"configurable": {}})
    result = await read_repo_file.read_repo_file("src/app.py")
    assert result["success"] is False

    config = {"configurable": {"chat_repo_owner": "acme", "chat_repo_name": "repo"}}
    monkeypatch.setattr("openswe.run_config.get_config", lambda: config)
    for tool, args in (
        (read_repo_file.read_repo_file, ("src/app.py",)),
        (search_repo_code.search_repo_code, ("foo",)),
    ):
        result = await tool(*args)
        assert result["error"] == "GitHub credentials unavailable; repository source was not read"


# --- graph factory guard -----------------------------------------------------


def test_chat_excludes_mutating_filesystem_tools() -> None:
    from openswe.chat import _EXCLUDED_TOOLS

    assert {"write_file", "edit_file", "delete", "execute"} <= _EXCLUDED_TOOLS


def test_chat_general_purpose_subagent_is_read_only() -> None:
    from openswe.chat import _chat_general_purpose_subagent

    spec = _chat_general_purpose_subagent()

    assert spec["name"] == "general-purpose"
    fs_middleware = [m for m in spec.get("middleware", []) if isinstance(m, FilesystemMiddleware)]
    assert len(fs_middleware) == 1
    enabled = fs_middleware[0]._enabled_tools
    assert enabled is not None
    assert {"write_file", "edit_file", "delete", "execute"}.isdisjoint(enabled)
    assert {"read_file", "ls", "glob", "grep"} <= enabled


@pytest.mark.asyncio
async def test_review_chat_rejects_thread_not_postable_for_this_pr(monkeypatch) -> None:
    async def no_accessible_threads(*args):
        return []

    monkeypatch.setattr(review_chat_api.pr_fixes, "find_pr_threads", no_accessible_threads)
    with pytest.raises(HTTPException) as error:
        await review_chat_api.proxy_review_chat_commands(
            "acme", "repo", 7, "octocat", "foreign-thread", b"{}"
        )
    assert error.value.status_code == 404
