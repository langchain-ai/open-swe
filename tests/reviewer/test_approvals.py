"""Approval criteria come from .open-swe/APPROVALS.md at the base commit; the repository's mode gates them."""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from fastapi import HTTPException

from agent.review.approval_fast_path import ChangedFile, _try_deterministic_approval, matching_rule
from agent.review.approval_rules import (
    ApprovalProgram,
    CompiledRules,
    DocumentationRule,
    approval_program,
    cached_approval_program,
    policy_hash,
)
from agent.review.approvals import (
    APPROVALS_MAX_CHARS,
    approval_mode_for,
    approval_policy_for_review,
    fetch_approvals_md,
)
from agent.review.routes import api_delete_review_style, api_update_review_style_prompt
from agent.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from agent.run_config import Repo, RunConfig
from agent.tools.manage_review_approval_mode import manage_review_approval_mode
from tests.conftest import FakeStore


def _github(status: int, text: str = "") -> AsyncMock:
    return AsyncMock(return_value=httpx2.Response(status, text=text))


async def test_fetch_reads_the_file_at_the_requested_ref() -> None:
    request = _github(200, "  Docs-only changes may be approved.\n")
    with patch("agent.github.repo_files.github_request", request):
        policy = await fetch_approvals_md("o", "r", "a" * 40, token="t")
    assert policy == "Docs-only changes may be approved."
    assert request.await_args is not None
    _client, method, url = request.await_args.args
    assert (method, url) == (
        "GET",
        "https://api.github.com/repos/o/r/contents/.open-swe/APPROVALS.md",
    )
    assert request.await_args.kwargs["params"] == {"ref": "a" * 40}


@pytest.mark.parametrize(
    ("status", "text"),
    [(404, ""), (200, "   \n"), (200, "x" * (APPROVALS_MAX_CHARS + 1)), (500, "boom")],
    ids=["missing", "empty", "oversized", "error"],
)
async def test_fetch_yields_no_policy_for_unusable_files(status: int, text: str) -> None:
    with patch("agent.github.repo_files.github_request", _github(status, text)):
        assert await fetch_approvals_md("o", "r", "main", token="t") is None


async def test_fetch_yields_no_policy_when_github_is_unreachable() -> None:
    failing = AsyncMock(side_effect=httpx2.ConnectError("down"))
    with patch("agent.github.repo_files.github_request", failing):
        assert await fetch_approvals_md("o", "r", "main", token="t") is None


async def test_unset_mode_is_dry_run_and_off_skips_the_file(fake_store: FakeStore) -> None:
    assert await approval_mode_for("o", "r") == "dry_run"
    fetch = AsyncMock(return_value="Docs only")
    with patch("agent.review.approvals.fetch_approvals_md", fetch):
        assert await approval_policy_for_review("o", "r", "b" * 40, token="t") == "Docs only"
        fetch.assert_awaited_once_with("o", "r", "b" * 40, token="t")

        await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="off"))
        fetch.reset_mock()
        assert await approval_policy_for_review("o", "r", "b" * 40, token="t") is None
        fetch.assert_not_awaited()


async def test_review_without_a_base_commit_has_no_policy(fake_store: FakeStore) -> None:
    fetch = AsyncMock(return_value="Docs only")
    with patch("agent.review.approvals.fetch_approvals_md", fetch):
        assert await approval_policy_for_review("o", "r", "", token="t") is None
    fetch.assert_not_awaited()


async def test_failed_mode_lookup_never_approves() -> None:
    with patch.object(REVIEW_STYLES, "get", AsyncMock(side_effect=RuntimeError("store down"))):
        assert await approval_mode_for("o", "r") == "dry_run"


async def test_only_admins_change_or_discard_a_mode(fake_store: FakeStore) -> None:
    await REVIEW_STYLES.create("o/r", "reader")
    reader = (
        patch("agent.review.routes.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch("agent.dashboard.deps.session_is_admin", return_value=False),
    )
    with reader[0], reader[1], pytest.raises(HTTPException) as error:
        await api_update_review_style_prompt(
            "o/r", ReviewStylePromptUpdate(approval_mode="off"), {"sub": "reader"}
        )
    assert error.value.status_code == 403
    assert await approval_mode_for("o", "r") == "dry_run"

    await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="approve"))
    with reader[0], reader[1], pytest.raises(HTTPException) as error:
        await api_delete_review_style("o/r", {"sub": "reader"})
    assert error.value.status_code == 403
    assert await approval_mode_for("o", "r") == "approve"


async def test_tool_rechecks_private_admin_and_repo_access(
    fake_store: FakeStore, grant_tool_access: Callable[..., None]
) -> None:
    grant_tool_access(admin=True, admin_thread=True)
    refused = await manage_review_approval_mode("set", "o/r", "approve")
    assert "not available in this thread" in str(refused["error"])
    grant_tool_access(admin=True, admin_surface=True)
    with (
        patch(
            "agent.run_config.get_config", return_value={"configurable": {"github_login": "admin"}}
        ),
        patch(
            "agent.tools.manage_review_approval_mode.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "No access")),
        ),
        pytest.raises(HTTPException),
    ):
        await manage_review_approval_mode("set", "private/repo", "approve")
    assert await REVIEW_STYLES.get("private/repo") is None


_POLICY = "Markdown under docs/ may be approved."
_RULE = DocumentationRule(
    paths=[], prefixes=["docs/"], max_changed_lines=100, policy_excerpt=_POLICY
)
_PROGRAM = ApprovalProgram(
    rules=[_RULE],
    explanation="Documentation only",
    policy_hash=policy_hash(_POLICY),
    generated_at="now",
)
_PATCH = "@@ -1 +1 @@\n-old\n+new"
_DIFF = (
    "diff --git a/docs/guide.md b/docs/guide.md\nindex abc..def 100644\n--- a/docs/guide.md\n+++ b/docs/guide.md\n"
    + _PATCH
    + "\n"
)
_FILE = ChangedFile(
    filename="docs/guide.md", status="modified", additions=1, deletions=1, patch=_PATCH
)


def test_documentation_rules_require_complete_regular_file_changes() -> None:
    assert matching_rule(_PROGRAM, [_FILE], _DIFF) == _RULE
    assert matching_rule(_PROGRAM, [_FILE], _DIFF.replace("100644", "120000")) is None
    assert matching_rule(_PROGRAM, [_FILE], _DIFF.replace("+new", "")) is None
    assert (
        matching_rule(
            _PROGRAM,
            [_FILE.model_copy(update={"filename": "docs/AGENTS.md"})],
            _DIFF.replace("guide.md", "AGENTS.md"),
        )
        is None
    )
    assert matching_rule(_PROGRAM, [_FILE, _FILE], _DIFF) is None
    added_patch = "@@ -0,0 +1 @@\n+new\n\\ No newline at end of file"
    added = _FILE.model_copy(update={"status": "added", "deletions": 0, "patch": added_patch})
    added_diff = (
        "diff --git a/docs/guide.md b/docs/guide.md\nnew file mode 100644\n"
        "index 0000000..abcdef0\n--- /dev/null\n+++ b/docs/guide.md\n" + added_patch + "\n"
    )
    assert matching_rule(_PROGRAM, [added], added_diff) == _RULE
    assert matching_rule(_PROGRAM, [added], added_diff.replace("100644", "120000")) is None


@pytest.mark.parametrize(
    "path",
    [
        "src/guide.md",
        "SECURITY.md",
        "docs/../src/guide.md",
        "docs/./guide.md",
        "docs//guide.md",
        "docs/guide\\name.md",
    ],
)
def test_evaluator_cannot_be_broadened_by_generated_rules(path: str) -> None:
    program = _PROGRAM.model_copy(update={"rules": [_RULE.model_copy(update={"paths": [path]})]})
    file = _FILE.model_copy(update={"filename": path})
    assert matching_rule(program, [file], _DIFF.replace("docs/guide.md", path)) is None


def test_diff_with_matching_counts_still_requires_complete_hunks_and_paths() -> None:
    patch = _PATCH.replace("@@ -1 +1 @@", "@@ -1,2 +1,2 @@")
    file = _FILE.model_copy(update={"patch": patch})
    assert matching_rule(_PROGRAM, [file], _DIFF.replace(_PATCH, patch)) is None
    assert (
        matching_rule(_PROGRAM, [_FILE], _DIFF.replace("+++ b/docs/guide.md", "+++ b/src/code.md"))
        is None
    )


async def test_compilation_is_cached_per_policy_and_manual_refresh_replaces_it(
    fake_store: FakeStore,
) -> None:
    structured = AsyncMock(return_value=CompiledRules(rules=[_RULE], explanation="docs"))
    model = MagicMock()
    model.with_structured_output.return_value.ainvoke = structured
    with patch("agent.review.approval_rules.make_model", return_value=model):
        assert await cached_approval_program("o", "r", _POLICY) is None
        structured.assert_not_awaited()
        first = await approval_program("o", "r", _POLICY)
        assert await cached_approval_program("o", "r", _POLICY) == first
        assert await approval_program("o", "r", _POLICY) == first
        assert structured.await_count == 1
        await approval_program("o", "r", _POLICY, refresh=True)
        assert structured.await_count == 2
        changed = await approval_program("o", "r", _POLICY + " Updated.")
        assert changed.policy_hash != first.policy_hash
        assert structured.await_count == 3


async def test_manual_refresh_reads_default_branch_and_preserves_mode(
    fake_store: FakeStore, grant_tool_access: Callable[..., None]
) -> None:
    grant_tool_access(admin=True, admin_surface=True)
    await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="off"))
    with (
        patch(
            "agent.run_config.get_config", return_value={"configurable": {"github_login": "admin"}}
        ),
        patch(
            "agent.tools.manage_review_approval_mode.require_repo_access_for_user",
            AsyncMock(return_value="t"),
        ),
        patch(
            "agent.review.approval_rules.fetch_approvals_md", AsyncMock(return_value=_POLICY)
        ) as policy,
        patch(
            "agent.review.approval_rules.approval_program", AsyncMock(return_value=_PROGRAM)
        ) as compile_rules,
    ):
        result = await manage_review_approval_mode("refresh", "o/r")
    policy.assert_awaited_once_with("o", "r", None, token="t")
    compile_rules.assert_awaited_once_with("o", "r", _POLICY, refresh=True)
    assert result["program"] == _PROGRAM.model_dump(mode="json")
    assert await approval_mode_for("o", "r") == "off"


@pytest.mark.parametrize(
    "gate",
    [
        "pass",
        "head_changed",
        "mode_disabled",
        "policy_changed",
        "missing_file",
        "uncached_policy",
        "program_replaced",
        "dry_run",
    ],
)
async def test_fast_path_revalidates_and_falls_back_without_approving(
    gate: str, fake_store: FakeStore
) -> None:
    from agent.review.approval_fast_path import _PullRequest

    cfg = RunConfig(
        thread_id="review",
        repo=Repo(owner="o", name="r"),
        pr_number=1,
        base_sha="base",
        head_sha="head",
    )
    pr = _PullRequest.model_validate(
        {
            "state": "open",
            "draft": False,
            "head": {"sha": "head", "ref": "branch"},
            "base": {"sha": "base", "ref": "main"},
            "user": {"login": "author"},
            "changed_files": 1,
        }
    )
    publish = AsyncMock(return_value={"id": 10})
    mode = "dry_run" if gate == "dry_run" else "approve"
    snapshot = AsyncMock(side_effect=[pr, None if gate == "head_changed" else pr])
    with (
        patch(
            "agent.review.approval_fast_path.approval_mode_for",
            AsyncMock(side_effect=[mode, "off" if gate == "mode_disabled" else mode]),
        ),
        patch(
            "agent.review.approval_fast_path.fetch_approvals_md",
            AsyncMock(side_effect=[_POLICY, "changed" if gate == "policy_changed" else _POLICY]),
        ),
        patch(
            "agent.review.approval_fast_path.cached_approval_program",
            AsyncMock(return_value=None if gate == "uncached_policy" else _PROGRAM)
            if gate != "program_replaced"
            else AsyncMock(side_effect=[_PROGRAM, None]),
        ),
        patch("agent.review.approval_rules.make_model") as model,
        patch("agent.review.approval_fast_path._eligible_snapshot", snapshot),
        patch(
            "agent.review.approval_fast_path._pages",
            AsyncMock(return_value=[] if gate == "missing_file" else [_FILE.model_dump()]),
        ),
        patch("agent.review.approval_fast_path.fetch_pr_diff", AsyncMock(return_value=_DIFF)),
        patch("agent.review.approval_fast_path.post_pull_request_review", publish),
        patch("agent.review.approval_fast_path.PullRequest.link_review", AsyncMock()),
        patch("agent.review.approval_fast_path.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.review.approval_fast_path.clear_review_started_comment", AsyncMock()),
        patch("agent.review.approval_fast_path.settle_review_check_run", AsyncMock()),
    ):
        assert await _try_deterministic_approval(cfg, token="t") is (gate in {"pass", "dry_run"})
        if gate == "pass":
            snapshot.side_effect = None
            snapshot.return_value = pr
            with (
                patch(
                    "agent.review.approval_fast_path.approval_mode_for",
                    AsyncMock(return_value="approve"),
                ),
                patch(
                    "agent.review.approval_fast_path.fetch_approvals_md",
                    AsyncMock(return_value=_POLICY),
                ),
            ):
                assert await _try_deterministic_approval(cfg, token="t") is True
    model.assert_not_called()
    if gate in {"pass", "dry_run"}:
        publish.assert_awaited_once()
        assert publish.await_args is not None
        assert publish.await_args.kwargs["event"] == ("COMMENT" if gate == "dry_run" else "APPROVE")
        assert publish.await_args.kwargs["head_sha"] == "head"
    else:
        publish.assert_not_awaited()


@pytest.mark.parametrize(
    "gate",
    [
        "pass",
        "pending",
        "empty",
        "malformed",
        "changes_requested",
        "findings_unavailable",
        "failure_on_later_status_page",
    ],
)
async def test_snapshot_never_treats_missing_or_blocked_evidence_as_success(gate: str) -> None:
    from agent.github.http import github_client
    from agent.review.approval_fast_path import _eligible_snapshot

    cfg = RunConfig(
        thread_id="review",
        repo=Repo(owner="o", name="r"),
        pr_number=1,
        base_sha="base",
        head_sha="head",
    )
    pr = {
        "state": "open",
        "draft": False,
        "head": {"sha": "head", "ref": "branch"},
        "base": {"sha": "base", "ref": "main"},
        "user": {"login": "author"},
        "changed_files": 1,
    }

    async def request(client: object, method: str, url: str) -> httpx2.Response:
        if "/check-runs" in url:
            payload = (
                {}
                if gate == "malformed"
                else {
                    "check_runs": []
                    if gate == "empty"
                    else [
                        {
                            "id": 2,
                            "name": "tests",
                            "status": "in_progress" if gate == "pending" else "completed",
                            "conclusion": "success",
                            "app": {"id": 1},
                        }
                    ]
                }
            )
        elif "/reviews" in url:
            payload = (
                [{"user": {"login": "human"}, "state": "CHANGES_REQUESTED"}]
                if gate == "changes_requested"
                else []
            )
        elif "/statuses?" in url:
            payload = (
                (
                    [{"context": f"check-{i}", "state": "success"} for i in range(100)]
                    if url.endswith("&page=1")
                    else [{"context": "hidden-failure", "state": "failure"}]
                )
                if gate == "failure_on_later_status_page"
                else []
            )
        else:
            payload = pr
        return httpx2.Response(200, json=payload, request=httpx2.Request("GET", url))

    with (
        patch("agent.review.approval_fast_path.github_request", side_effect=request),
        patch(
            "agent.review.approval_fast_path.list_findings",
            AsyncMock(side_effect=RuntimeError("store down"))
            if gate == "findings_unavailable"
            else AsyncMock(return_value=[]),
        ),
        patch(
            "agent.review.approval_fast_path.fetch_unresolved_review_threads",
            AsyncMock(return_value=[]),
        ),
        patch(
            "agent.review.approval_fast_path.get_thread_metadata",
            AsyncMock(return_value={"review_check_run_id": 1}),
        ),
        patch(
            "agent.review.approval_fast_path.fetch_required_checks", AsyncMock(return_value=set())
        ),
    ):
        async with github_client(token="t") as client:
            if gate in {"malformed", "findings_unavailable"}:
                with pytest.raises((ValueError, RuntimeError)):
                    await _eligible_snapshot(client, cfg, "t")
            else:
                assert (await _eligible_snapshot(client, cfg, "t") is not None) is (gate == "pass")


@pytest.mark.parametrize(
    "threads",
    [
        {"nodes": [], "pageInfo": {"hasNextPage": False}},
        {"nodes": []},
        {"nodes": [None], "pageInfo": {"hasNextPage": False}},
    ],
)
async def test_approval_requires_complete_review_thread_data(threads: dict[str, object]) -> None:
    from agent.github.http import github_client
    from agent.github.pull_request_status import fetch_unresolved_review_threads

    response = httpx2.Response(
        200,
        json={"data": {"repository": {"pullRequest": {"reviewThreads": threads}}}},
        request=httpx2.Request("POST", "https://api.github.com/graphql"),
    )
    with patch("agent.github.pull_request_status.github_request", AsyncMock(return_value=response)):
        async with github_client(token="t") as client:
            result = await fetch_unresolved_review_threads(client, "o", "r", 1, strict=True)
    assert result == ([] if threads.get("pageInfo") and threads["nodes"] == [] else None)
