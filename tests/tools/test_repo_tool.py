from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.repo_tool import _split_full_name, repo
from agent.utils.repo_clone import (
    build_clone_script,
    cache_path_for,
    cache_root_for,
    parse_clone_result,
)


def test_cache_path_is_owner_scoped_and_inside_the_work_dir() -> None:
    assert cache_path_for("/workspace", "acme", "tools") == "/workspace/.repo-cache/acme/tools"
    # Two owners can each have a repo named "tools"; the paths must not collide.
    assert cache_path_for("/workspace", "other", "tools") != cache_path_for(
        "/workspace", "acme", "tools"
    )


def test_cache_root_follows_the_work_dir() -> None:
    # The local provider's work dir is a real host directory, so the cache must
    # stay inside it rather than at an absolute path like /opt.
    assert cache_root_for("/tmp/sbx") == "/tmp/sbx/.repo-cache"
    # Hidden, so it stays out of `ls` in an otherwise empty work dir.
    assert cache_root_for("/tmp/sbx").rsplit("/", 1)[-1].startswith(".")


def test_clone_script_prefers_cache_but_falls_back_to_github() -> None:
    script = build_clone_script(work_dir="/workspace", owner="acme", name="tools")
    # Taking a baked checkout is a rename, not a copy: O(1) whatever the size.
    assert 'mv /workspace/.repo-cache/acme/tools "$DEST"' in script
    # Falls through to a network clone when the move fails or nothing is baked.
    assert "2>/dev/null; then" in script
    assert 'git clone https://github.com/acme/tools.git "$DEST"' in script


def test_clone_script_always_fetches_after_local_clone() -> None:
    script = build_clone_script(work_dir="/workspace", owner="acme", name="tools")
    # The mirror is only as fresh as the last snapshot build.
    assert "git fetch origin --prune" in script


def test_clone_script_records_whether_the_fetch_succeeded() -> None:
    script = build_clone_script(work_dir="/workspace", owner="acme", name="tools")
    # A failed fetch must not silently pass for up-to-date.
    assert "FETCHED=true" in script
    assert "FETCHED=false" in script
    assert "fetched=$FETCHED" in script
    assert "git fetch origin --prune --quiet || true" not in script


def test_clone_script_reuses_an_existing_checkout() -> None:
    script = build_clone_script(work_dir="/workspace", owner="acme", name="tools")
    assert 'if [ -d "$DEST/.git" ]' in script
    assert "SOURCE=existing" in script
    # Never clobber a checkout that may hold uncommitted work.
    assert "rm -rf" not in script


def test_clone_script_will_not_reuse_a_different_repo_of_the_same_name() -> None:
    # org-a/tools then org-b/tools must not hand back the first checkout, or
    # the agent silently edits the wrong code.
    script = build_clone_script(work_dir="/workspace", owner="acme", name="tools")
    assert "remote get-url origin" in script
    assert "[:/]acme/tools" in script
    assert "DEST=/workspace/acme-tools" in script


def test_clone_script_checks_out_explicit_ref() -> None:
    script = build_clone_script(work_dir="/workspace", owner="acme", name="tools", ref="v1.2.3")
    assert "git checkout --force v1.2.3" in script


def test_clone_script_defaults_to_the_default_branch() -> None:
    script = build_clone_script(work_dir="/workspace", owner="acme", name="tools")
    assert "refs/remotes/origin/HEAD" in script
    assert '"origin/$DEFAULT"' in script


def test_clone_script_quotes_hostile_input() -> None:
    script = build_clone_script(work_dir="/work dir", owner="acme", name="a;rm -rf /")
    assert "'/work dir/a;rm -rf /'" in script
    assert "rm -rf /\n" not in script


def test_parse_clone_result_reads_the_marker_line() -> None:
    fields = parse_clone_result(
        "cloning...\nOPENSWE_CLONE source=cache fetched=true path=/workspace/tools head=abc123\n"
    )
    assert fields == {
        "source": "cache",
        "fetched": "true",
        "path": "/workspace/tools",
        "head": "abc123",
    }


def test_parse_clone_result_ignores_output_without_a_marker() -> None:
    assert parse_clone_result("fatal: repository not found\n") == {}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("acme/tools", ("acme", "tools")),
        ("https://github.com/acme/tools", ("acme", "tools")),
        ("github.com/acme/tools.git", ("acme", "tools")),
        ("acme", ("", "")),
        ("acme/tools/extra", ("", "")),
        ("", ("", "")),
    ],
)
def test_split_full_name(value: str, expected: tuple[str, str]) -> None:
    assert _split_full_name(value) == expected


@pytest.mark.asyncio
async def test_repo_tool_rejects_a_malformed_name() -> None:
    result = await repo("clone", "not-a-repo")
    assert result["success"] is False
    assert "owner/name" in result["error"]


def _sandbox(stdout: str, exit_code: int = 0):
    backend = SimpleNamespace()
    backend.aexecute = AsyncMock(return_value=SimpleNamespace(output=stdout, exit_code=exit_code))
    return backend


@pytest.mark.asyncio
async def test_repo_tool_records_the_clone_and_returns_the_path() -> None:
    backend = _sandbox(
        "OPENSWE_CLONE source=cache fetched=true path=/workspace/tools head=abc123\n"
    )
    record = AsyncMock()
    with (
        patch(
            "agent.tools.repo_tool.get_config",
            return_value={"configurable": {"thread_id": "t-1"}},
        ),
        patch("agent.server.ensure_sandbox_for_thread", AsyncMock(return_value=backend)),
        patch(
            "agent.tools.repo_tool.aresolve_sandbox_work_dir", AsyncMock(return_value="/workspace")
        ),
        patch("agent.tools.repo_tool.record_repo_clone", record),
    ):
        result = await repo("clone", "acme/tools")

    assert result["success"] is True
    assert result["path"] == "/workspace/tools"
    assert result["source"] == "cache"
    assert result["fetched"] is True
    record.assert_awaited_once_with("acme", "tools")


@pytest.mark.asyncio
async def test_repo_tool_does_not_record_a_failed_clone() -> None:
    backend = _sandbox("fatal: repository not found\n", exit_code=1)
    record = AsyncMock()
    with (
        patch(
            "agent.tools.repo_tool.get_config",
            return_value={"configurable": {"thread_id": "t-1"}},
        ),
        patch("agent.server.ensure_sandbox_for_thread", AsyncMock(return_value=backend)),
        patch(
            "agent.tools.repo_tool.aresolve_sandbox_work_dir", AsyncMock(return_value="/workspace")
        ),
        patch("agent.tools.repo_tool.record_repo_clone", record),
    ):
        result = await repo("clone", "acme/tools")

    assert result["success"] is False
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_repo_tool_does_not_record_when_the_marker_is_missing() -> None:
    # Exit 0 with no marker means the script did not get far enough to matter.
    backend = _sandbox("some unrelated chatter\n")
    record = AsyncMock()
    with (
        patch(
            "agent.tools.repo_tool.get_config",
            return_value={"configurable": {"thread_id": "t-1"}},
        ),
        patch("agent.server.ensure_sandbox_for_thread", AsyncMock(return_value=backend)),
        patch(
            "agent.tools.repo_tool.aresolve_sandbox_work_dir", AsyncMock(return_value="/workspace")
        ),
        patch("agent.tools.repo_tool.record_repo_clone", record),
    ):
        result = await repo("clone", "acme/tools")

    assert result["success"] is False
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_repo_tool_rejects_an_unknown_action() -> None:
    result = await repo("update", "acme/tools")  # type: ignore[arg-type]
    assert result["success"] is False
    assert "unknown action" in result["error"]


@pytest.mark.asyncio
async def test_repo_tool_surfaces_a_stale_checkout() -> None:
    # Exit 0 with a failed fetch: usable, but behind. The caller must be told.
    backend = _sandbox("OPENSWE_CLONE source=cache fetched=false path=/workspace/tools head=abc\n")
    with (
        patch(
            "agent.tools.repo_tool.get_config",
            return_value={"configurable": {"thread_id": "t-1"}},
        ),
        patch("agent.server.ensure_sandbox_for_thread", AsyncMock(return_value=backend)),
        patch(
            "agent.tools.repo_tool.aresolve_sandbox_work_dir", AsyncMock(return_value="/workspace")
        ),
        patch("agent.tools.repo_tool.record_repo_clone", AsyncMock()),
    ):
        result = await repo("clone", "acme/tools")

    assert result["success"] is True
    assert result["fetched"] is False
