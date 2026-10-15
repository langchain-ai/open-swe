from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from openswe.review import eval_jobs
from openswe.review.eval_jobs import (
    ReviewerEvalConfig,
    get_reviewer_eval_status,
    resolve_eval_config,
    start_reviewer_eval,
)
from openswe.review.eval_report import REPORT_PATH, REPORT_TOKEN_HEADER
from openswe.review.eval_report_routes import router
from openswe.users.models import User
from openswe.users.records import user_id_for_login


@pytest.fixture
async def people(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    await User.sign_in("github", "1", login="alice")
    await User.sign_in("github", "2", login="bob")


@pytest.fixture
def launches(people: None, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The report token each launched sandbox was given."""
    tokens: list[str] = []

    async def launch(
        _config: ReviewerEvalConfig, _limit: int | None, report_token: str, _ref: str
    ) -> str:
        tokens.append(report_token)
        return f"sandbox-{len(tokens)}"

    monkeypatch.setattr(eval_jobs, "_launch_sandbox", launch)
    monkeypatch.setattr(eval_jobs, "backend_build_info", lambda: {"commit": "abc123"})
    return tokens


@pytest.fixture
async def sandbox() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _report(status: str, completed: int) -> dict[str, object]:
    return {"status": status, "completed_examples": completed, "total_examples": 5}


async def test_only_the_launched_runs_sandbox_reports_its_progress(
    launches: list[str], sandbox: httpx.AsyncClient
) -> None:
    await start_reviewer_eval(resolve_eval_config(), 5, user_id_for_login("alice"))
    first = launches[0]

    for headers in ({}, {REPORT_TOKEN_HEADER: "guess"}):
        response = await sandbox.post(REPORT_PATH, json=_report("failed", 0), headers=headers)
        assert response.status_code == 401
    response = await sandbox.post(
        REPORT_PATH, json=_report("running", 2), headers={REPORT_TOKEN_HEADER: first}
    )
    assert response.status_code == 204
    status = await get_reviewer_eval_status()
    assert (status.status, status.worker_id, status.created_by) == ("running", "sandbox-1", "alice")
    assert status.progress is not None and status.progress.completed == 2

    await sandbox.post(
        REPORT_PATH, json=_report("completed", 5), headers={REPORT_TOKEN_HEADER: first}
    )
    await start_reviewer_eval(resolve_eval_config(), None, user_id_for_login("bob"))
    await sandbox.post(REPORT_PATH, json=_report("failed", 5), headers={REPORT_TOKEN_HEADER: first})
    status = await get_reviewer_eval_status()
    assert (status.status, status.worker_id, status.created_by) == ("starting", "sandbox-2", "bob")
