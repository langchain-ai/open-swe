import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.review import approval_settings as settings
from agent.review.approval import (
    ApprovalEvidence,
    ApprovalFacts,
    CriterionEvidence,
    Gate,
    PolicyRules,
    evaluate_policy,
)
from tests.conftest import FakeStore


@pytest.fixture(autouse=True)
def store_and_lock(fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = asyncio.Lock()

    @asynccontextmanager
    async def policy_lock() -> AsyncIterator[None]:
        async with lock:
            yield

    monkeypatch.setattr(settings, "_policy_lock", policy_lock)


def definition(
    score: int = 2, criteria: str = "## Scope\nOnly documentation changes."
) -> settings.PolicyDefinition:
    return settings.PolicyDefinition(
        rules=PolicyRules(max_risk_score=score, required_checks=["tests"]),
        criteria_markdown=criteria,
    )


async def save(
    repository: str | None, policy: settings.PolicyDefinition | None
) -> settings.PolicySettingsView:
    current = await settings.get_policy_settings(repository)
    return await settings.save_policy_settings(
        repository, policy=policy, expected_version=current.effective_version, login="admin"
    )


async def test_repository_policy_replaces_shared_rules_and_criteria() -> None:
    shared = definition()
    shared.rules.human_review_paths = ["auth/*"]
    await save(None, shared)
    local = definition(5, "## Scope\nAn owner has checked the documentation.")
    local.rules.minimum_confidence = "low"
    local.rules.required_checks = ["docs"]
    local.rules.human_review_paths = ["billing/*"]
    view = await save("Org/Repo", local)
    assert view.repository == "org/repo"
    assert view.effective_rules.max_risk_score == 5
    assert view.effective_rules.minimum_confidence == "low"
    assert view.effective_rules.required_checks == ["docs"]
    assert view.effective_rules.human_review_paths == ["billing/*"]
    snapshot = await settings.load_policy_snapshot("org/repo", base_sha="b" * 40, head_sha="a" * 40)
    assert [c.id for c in snapshot.criteria] == ["repository:scope"]
    assert "Only documentation changes." not in snapshot.content
    assert "An owner has checked" in snapshot.content
    evaluation = evaluate_policy(
        policy=snapshot,
        evidence=ApprovalEvidence(
            policy_version=snapshot.version,
            base_sha="b" * 40,
            head_sha="a" * 40,
            review_complete=True,
            criteria=[
                CriterionEvidence(
                    id="repository:scope", status="pass", evidence="Owner reviewed the change."
                )
            ],
        ),
        facts=ApprovalFacts(
            current_head_sha="a" * 40,
            current_base_sha="b" * 40,
            ready=True,
            changed_paths=["auth/docs.md"],
            ci=Gate(id="ci", title="Checks", status="pass", evidence="Checks passed."),
            change_requests=Gate(
                id="change_requests", title="Reviews", status="pass", evidence="None outstanding."
            ),
        ),
        head_sha="a" * 40,
        risk_score=4,
        confidence="low",
        limitations=[],
        open_findings=0,
    )
    assert evaluation.decision == "would_approve"


async def test_shared_edits_do_not_change_an_overridden_repository_policy() -> None:
    await save(None, definition(2))
    before = await save("org/repo", definition(4))
    await save(None, definition(1, "## New shared criterion\nRequires human review."))
    after = await settings.get_policy_settings("org/repo")
    assert after.shared_policy.rules.max_risk_score == 1
    assert after.effective_rules.max_risk_score == 4
    assert after.effective_version == before.effective_version
    updated = await settings.save_policy_settings(
        "org/repo", policy=definition(5), expected_version=before.effective_version, login="admin"
    )
    assert updated.effective_rules.max_risk_score == 5


async def test_reset_inherits_latest_shared_policy_without_mutating_old_snapshot() -> None:
    await save(None, definition(3))
    first = await save("org/repo", definition(1))
    snapshot = await settings.load_policy_snapshot("org/repo", base_sha="b" * 40, head_sha="a" * 40)
    await save(None, definition(2))
    reset = await save("org/repo", None)
    assert reset.policy is None
    assert reset.effective_rules.max_risk_score == 2
    assert snapshot.rules.max_risk_score == 1
    assert reset.effective_version != first.effective_version
    assert first.revision is not None
    historical = await settings.REVISIONS.get(first.revision)
    assert historical is not None and historical.policy is not None
    assert historical.policy.rules.max_risk_score == 1
    assert historical.updated_by == "admin"


async def test_concurrent_and_stale_shared_edits_cannot_overwrite() -> None:
    current = await settings.get_policy_settings(None)
    results = await asyncio.gather(
        *(
            settings.save_policy_settings(
                None,
                policy=definition(score),
                expected_version=current.effective_version,
                login="admin",
            )
            for score in [1, 3]
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, settings.PolicyConflict) for result in results) == 1
    repo_view = await settings.get_policy_settings("org/repo")
    await save(None, definition(2))
    with pytest.raises(settings.PolicyConflict):
        await settings.save_policy_settings(
            "org/repo",
            policy=definition(),
            expected_version=repo_view.effective_version,
            login="admin",
        )


async def test_reverting_content_still_invalidates_old_evidence() -> None:
    first = await save(None, definition(2))
    await save(None, definition(1))
    reverted = await save(None, definition(2))
    assert first.effective_version != reverted.effective_version


async def test_rules_only_repository_policy_is_valid_but_empty_shared_policy_is_not() -> None:
    shared = definition()
    shared.rules.human_review_paths = ["auth/*"]
    await save(None, shared)
    local = definition(4, "")
    local.rules.required_checks = []
    local.rules.human_review_paths = []
    await save("org/repo", local)
    snapshot = await settings.load_policy_snapshot("org/repo", base_sha="b" * 40, head_sha="a" * 40)
    assert snapshot.criteria == []
    assert snapshot.rules.required_checks == []
    assert snapshot.rules.human_review_paths == []
    with pytest.raises(ValueError, match="criteria"):
        await save(None, definition(1, ""))


@pytest.mark.parametrize(
    "markdown",
    ["## Scope\n", "## Same\nA\n## Same\nB", "+++\nmax_risk_score=5\n+++\n## Scope\nSmall changes"],
)
async def test_invalid_criteria_are_rejected_before_saving(markdown: str) -> None:
    with pytest.raises(ValueError):
        await save("org/repo", definition(2, markdown))
    assert (await settings.get_policy_settings("org/repo")).policy is None


async def test_store_outage_does_not_silently_use_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings.CURRENT, "get", AsyncMock(side_effect=RuntimeError("store unavailable"))
    )
    with pytest.raises(RuntimeError, match="store unavailable"):
        await settings.load_policy_snapshot("org/repo", base_sha="b" * 40, head_sha="a" * 40)


async def test_api_requires_admin_and_repository_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.review import approval_routes

    monkeypatch.setattr(
        approval_routes, "session_is_admin", lambda session: session["sub"] == "admin"
    )
    monkeypatch.setattr(
        approval_routes,
        "require_repo_access_for_user",
        AsyncMock(side_effect=HTTPException(404, "repository not found")),
    )
    body = settings.PolicySettingsUpdate(policy=definition(), expected_version="stale")
    with pytest.raises(HTTPException) as denied:
        await approval_routes.update_approval_policy(
            body, repository=None, session={"sub": "reader"}
        )
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as no_access:
        await approval_routes.read_approval_policy(
            repository="private/repo", session={"sub": "admin"}
        )
    assert no_access.value.status_code == 404
    with pytest.raises(HTTPException) as no_write_access:
        await approval_routes.update_approval_policy(
            body, repository="private/repo", session={"sub": "admin"}
        )
    assert no_write_access.value.status_code == 404
    assert (await settings.get_policy_settings(None)).revision is None


async def test_api_save_reset_and_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.review import approval_routes

    monkeypatch.setattr(
        approval_routes, "session_is_admin", lambda session: session["sub"] == "admin"
    )
    current = await approval_routes.read_approval_policy(repository=None, session={"sub": "reader"})
    assert not current.can_edit
    update = settings.PolicySettingsUpdate(
        policy=definition(1), expected_version=current.effective_version
    )
    saved = await approval_routes.update_approval_policy(
        update, repository=None, session={"sub": "admin"}
    )
    assert saved.can_edit and saved.updated_by == "admin"
    assert saved.effective_rules.max_risk_score == 1
    with pytest.raises(HTTPException) as stale:
        await approval_routes.update_approval_policy(
            update, repository=None, session={"sub": "admin"}
        )
    assert stale.value.status_code == 409
    reset = await approval_routes.update_approval_policy(
        settings.PolicySettingsUpdate(policy=None, expected_version=saved.effective_version),
        repository=None,
        session={"sub": "admin"},
    )
    assert reset.policy is None and reset.effective_rules.max_risk_score == 2


async def test_agent_tool_requires_private_admin_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    tool = importlib.import_module("agent.tools.manage_review_approval_policy")
    monkeypatch.setattr(
        tool, "require_private_admin_surface", AsyncMock(return_value="Only admins")
    )
    with pytest.raises(ValueError, match="Only admins"):
        await tool.manage_review_approval_policy(action="read")
    monkeypatch.setattr(tool, "require_private_admin_surface", AsyncMock(return_value=None))
    monkeypatch.setattr(tool, "private_credential_login", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="private thread"):
        await tool.manage_review_approval_policy(action="read")
    monkeypatch.setattr(tool, "private_credential_login", AsyncMock(return_value="admin"))
    current = await tool.manage_review_approval_policy(action="read")
    saved = await tool.manage_review_approval_policy(
        action="save", policy=definition(1), expected_version=str(current["effective_version"])
    )
    assert settings.PolicySettingsView.model_validate(saved).effective_rules.max_risk_score == 1


async def test_api_rejects_cross_origin_and_invalid_policy_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from fastapi import FastAPI

    from agent.dashboard.oauth import require_session
    from agent.review import approval_routes

    app = FastAPI()
    app.include_router(approval_routes.router)
    app.dependency_overrides[require_session] = lambda: {"sub": "admin"}
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:2024")
    monkeypatch.setattr(approval_routes, "session_is_admin", lambda session: True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:2024"
    ) as client:
        current = settings.PolicySettingsView.model_validate(
            (await client.get("/review-approval-policy")).json()
        )
        body = {"policy": definition(1).model_dump(), "expected_version": current.effective_version}
        denied = await client.put(
            "/review-approval-policy", json=body, headers={"Origin": "https://attacker.example"}
        )
        assert denied.status_code == 403
        invalid = await client.put(
            "/review-approval-policy",
            json={
                "policy": {
                    "rules": {"max_risk_score": 9},
                    "criteria_markdown": "## Scope\nSmall changes",
                },
                "expected_version": current.effective_version,
            },
            headers={"Origin": "http://localhost:2024"},
        )
        assert invalid.status_code == 422
    assert (await settings.get_policy_settings()).revision is None
