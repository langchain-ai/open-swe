"""Approval criteria come from .open-swe on the default branch; the repository's mode gates them."""

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from fastapi import HTTPException

from openswe.review.approvals import (
    APPROVALS_MAX_CHARS,
    ApprovalPolicy,
    approval_mode_for,
    approval_policy_for_review,
)
from openswe.review.routes import api_delete_review_style, api_update_review_style_prompt
from openswe.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from openswe.tools.manage_review_approval_mode import manage_review_approval_mode


def _github(status: int, text: str = "", rules: dict[str, str | None] | None = None) -> AsyncMock:
    async def respond(_client: object, method: str, url: str, **kwargs: object) -> httpx2.Response:
        request = httpx2.Request(method, url)
        if url.endswith("/graphql"):
            entries = [
                {"path": path, "type": "blob", "object": {"text": text}}
                for path, text in (rules or {}).items()
            ]
            tree = {"entries": entries} if rules is not None else None
            return httpx2.Response(
                200, json={"data": {"repository": {"object": tree}}}, request=request
            )
        return httpx2.Response(status, text=text, request=request)

    return AsyncMock(side_effect=respond)


async def test_fetch_reads_the_criteria_from_the_default_branch() -> None:
    request = _github(200, "  Docs-only changes may be approved.\n", rules={})
    with patch("openswe.github.http.github_request", request):
        policy = await ApprovalPolicy.load("o", "r", token="t")
    assert policy.render() == "Docs-only changes may be approved."
    calls = {call.args[2]: call for call in request.await_args_list}
    contents = calls["https://api.github.com/repos/o/r/contents/.open-swe/APPROVALS.md"]
    assert contents.kwargs["params"] is None
    graphql = calls["https://api.github.com/graphql"].kwargs["json"]["variables"]
    assert graphql["expression"] == "HEAD:.open-swe/auto-approve-rules"


async def test_rules_apply_by_their_file_scopes() -> None:
    rules = {
        ".open-swe/auto-approve-rules/any.md": "Typo fixes may be approved.",
        ".open-swe/auto-approve-rules/docs.md": "---\nmatch-all-files:\n  - docs/*\n  - '*.md'\n---\nDocs may be approved.\n",
        ".open-swe/auto-approve-rules/auth.md": "---\nmatch-any-files: [auth/*]\n---\nAuth needs human review.",
        ".open-swe/auto-approve-rules/notes.txt": "Not a rule.",
    }
    with patch("openswe.github.http.github_request", _github(404, rules=rules)):
        policy = await ApprovalPolicy.load("o", "r", token="t")

    def applied(changed: list[str] | None) -> list[str]:
        return [rule.path.rsplit("/", 1)[-1] for rule in policy.for_changes(changed).rules]

    assert applied(["docs/guide/a.md", "README.md"]) == ["any.md", "docs.md"]
    assert applied(["docs/a.md", "src/app.py"]) == ["any.md"]
    assert applied(["auth/login.py", "src/app.py"]) == ["any.md", "auth.md"]
    assert applied(None) == ["any.md", "auth.md"]


async def test_one_unusable_rule_fails_the_whole_policy_closed() -> None:
    # The dropped rule may be the one requiring human review, so nothing may apply without it.
    rules = {
        ".open-swe/auto-approve-rules/any.md": "Everything may be approved.",
        ".open-swe/auto-approve-rules/restrict.md": "---\npaths: [auth/*]\n---\nAuth needs human review.",
        ".open-swe/auto-approve-rules/binary.md": None,
    }
    with patch("openswe.github.http.github_request", _github(404, rules=rules)):
        policy = await ApprovalPolicy.load("o", "r", token="t")
    assert policy.configured and policy.render() is None
    assert [error.split("`")[1].rsplit("/", 1)[-1] for error in policy.errors] == [
        "binary.md",
        "restrict.md",
    ]


@pytest.mark.parametrize(
    ("status", "text", "broken"),
    [
        (404, "", False),
        (200, "   \n", False),
        (200, "x" * (APPROVALS_MAX_CHARS + 1), True),
        (500, "boom", True),
    ],
    ids=["missing", "empty", "oversized", "error"],
)
async def test_fetch_yields_no_policy_for_unusable_files(
    status: int, text: str, broken: bool
) -> None:
    with patch("openswe.github.http.github_request", _github(status, text)):
        policy = await ApprovalPolicy.load("o", "r", token="t")
    assert policy.render() is None
    assert bool(policy.errors) is broken


async def test_fetch_yields_no_policy_when_github_is_unreachable() -> None:
    failing = AsyncMock(side_effect=httpx2.ConnectError("down"))
    with patch("openswe.github.http.github_request", failing):
        policy = await ApprovalPolicy.load("o", "r", token="t")
    assert policy.render() is None and policy.errors


async def test_config_errors_cover_changed_settings_and_rules_at_the_head() -> None:
    head = "b" * 40
    files = [
        {"filename": ".open-swe/settings.json"},
        {"filename": ".open-swe/auto-approve-rules/x.md"},
    ]
    repo = AsyncMock()
    repo.pull_request = lambda number: AsyncMock(files=AsyncMock(return_value=files))
    repo.read_file = AsyncMock(return_value='{"supplementaryReviews": [{"id": "Bad id"}]}')
    repo.read_dir = AsyncMock(
        return_value={".open-swe/auto-approve-rules/x.md": "---\n- a\n---\nBody"}
    )
    repo.full_name = "o/r"
    errors = await ApprovalPolicy.config_errors(repo, 1, head)
    assert len(errors) == 2
    assert errors[0].startswith("`.open-swe/auto-approve-rules/x.md`: invalid frontmatter")
    assert errors[1].startswith("`.open-swe/settings.json`: supplementaryReviews[0]")
    repo.read_dir.assert_awaited_with(".open-swe/auto-approve-rules", head)


async def test_unset_mode_is_dry_run_and_off_skips_the_file(registry_db: None) -> None:
    assert await approval_mode_for("o", "r") == "dry_run"
    fetch = AsyncMock(return_value=ApprovalPolicy(approvals_md="Docs only"))
    with patch.object(ApprovalPolicy, "fetch", fetch):
        assert await approval_policy_for_review("o", "r", 1, token="t") == fetch.return_value
        fetch.assert_awaited_once()

        await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="off"))
        fetch.reset_mock()
        assert await approval_policy_for_review("o", "r", 1, token="t") is None
        fetch.assert_not_awaited()


async def test_failed_mode_lookup_never_approves() -> None:
    with patch.object(REVIEW_STYLES, "get", AsyncMock(side_effect=RuntimeError("store down"))):
        assert await approval_mode_for("o", "r") == "dry_run"


async def test_only_admins_change_or_discard_a_mode(registry_db: None) -> None:
    await REVIEW_STYLES.create("o/r", "reader")
    reader = (
        patch("openswe.review.routes.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch("openswe.dashboard.deps.session_is_admin", return_value=False),
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
    registry_db: None, grant_tool_access: Callable[..., None]
) -> None:
    grant_tool_access(admin=True, admin_thread=True)
    refused = await manage_review_approval_mode("set", "o/r", "approve")
    assert "not available in this thread" in str(refused["error"])
    grant_tool_access(admin=True, admin_surface=True)
    with (
        patch(
            "openswe.run_config.get_config",
            return_value={"configurable": {"github_login": "admin"}},
        ),
        patch(
            "openswe.tools.manage_review_approval_mode.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "No access")),
        ),
        pytest.raises(HTTPException),
    ):
        await manage_review_approval_mode("set", "private/repo", "approve")
    assert await REVIEW_STYLES.get("private/repo") is None
