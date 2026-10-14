from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from openswe.database.store_imports import StoreImport
from openswe.review import eval_jobs
from openswe.review.eval_jobs import (
    ReviewerEvalConfig,
    get_reviewer_eval_status,
    resolve_eval_config,
    start_reviewer_eval,
)
from openswe.review.eval_report import REPORT_PATH, REPORT_TOKEN_HEADER
from openswe.review.eval_report_routes import router
from openswe.review.import_eval_runs import import_store_reviewer_eval
from tests.conftest import FakeStore


@pytest.fixture
def launches(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> list[str]:
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
    await start_reviewer_eval(resolve_eval_config(), 5, "alice")
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
    await start_reviewer_eval(resolve_eval_config(), None, "bob")
    await sandbox.post(REPORT_PATH, json=_report("failed", 5), headers={REPORT_TOKEN_HEADER: first})
    status = await get_reviewer_eval_status()
    assert (status.status, status.worker_id, status.created_by) == ("starting", "sandbox-2", "bob")


async def test_store_import_copies_the_latest_eval_and_takes_newer_saves(
    registry_db: None, fake_store: FakeStore
) -> None:
    record = {
        "name": "reviewer",
        "status": "completed",
        "run_name": "nightly",
        "langsmith_project": "evals",
        "limit": 3,
        "config_snapshot": {"experiment_prefix": "nightly", "langsmith_project": "evals"},
        "started_at": "2026-10-01T00:00:00+00:00",
        "finished_at": "2026-10-01T01:00:00+00:00",
        "created_by": "alice",
        "pid": None,
        "experiment_url": "https://smith.langchain.com/o/1/datasets/2/compare",
        "worker_id": "sandbox-old",
        "heartbeat": "2026-10-01T01:00:00+00:00",
        "progress": {"completed": 3, "total": 3},
        "trigger": "sandbox",
        "updated_at": "2026-10-01T01:00:00+00:00",
    }
    fake_store.seed(["evals"], "reviewer", record)

    assert await import_store_reviewer_eval() == StoreImport(moved=1)
    status = await get_reviewer_eval_status()
    assert (status.status, status.run_name, status.limit, status.worker_id) == (
        "completed",
        "nightly",
        3,
        "sandbox-old",
    )
    assert await import_store_reviewer_eval() == StoreImport(waiting=1)

    fake_store.seed(
        ["evals"],
        "reviewer",
        {**record, "status": "failed", "updated_at": datetime.now(UTC).isoformat()},
    )
    assert await import_store_reviewer_eval() == StoreImport(moved=1)
    assert (await get_reviewer_eval_status()).status == "failed"
