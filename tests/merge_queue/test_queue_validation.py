from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import pytest

from openswe.github.http import RepoClient
from openswe.github.queue import Artifact, ValidationJob, ValidationRun
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.dispatch import DispatchGateway, DispatchRejected
from openswe.merge_queue.git import Candidate, PullHead
from openswe.merge_queue.models import Attempt, Batch, Lease
from openswe.merge_queue.store import MergeQueue, ValidationWork
from openswe.merge_queue.validation import CandidateValidation
from openswe.merge_queue.validation_contract import ValidationPolicy, ValidationRequest


@pytest.fixture
def validation() -> tuple[CandidateValidation, ValidationWork, Lease]:
    batch_id, attempt_id = uuid7(), uuid7()
    candidate = Candidate(
        repository="https://github.com/acme/widgets.git",
        branch="main",
        base_sha="a" * 40,
        pulls=(PullHead(number=1, sha="b" * 40),),
        merges=("c" * 40,),
        tree_sha="d" * 40,
    )
    request = ValidationRequest(
        repository="acme/widgets",
        batch_id=batch_id,
        attempt_id=attempt_id,
        candidate_sha=candidate.sha,
        workflow_sha=candidate.base_sha,
        dispatch_actor_id=123,
    )
    work = ValidationWork(
        batch=Mock(
            spec=Batch,
            id=batch_id,
            base_sha=candidate.base_sha,
            candidate=candidate.model_dump(mode="json"),
        ),
        attempt=Mock(
            spec=Attempt,
            id=attempt_id,
            created_at=datetime.now(UTC),
            deadline=datetime.now(UTC) + timedelta(minutes=10),
        ),
        request=request,
    )
    queue = Mock(spec=MergeQueue, mode="custom")
    queue.repository_name = AsyncMock(return_value=request.repository)
    queue.advance_validation = AsyncMock(return_value=work)
    queue.bind_validation_run = AsyncMock()
    queue.finish_validation = AsyncMock(return_value=True)
    queue.invalidate = AsyncMock()
    run = ValidationRun(
        id=42,
        actor_id=123,
        triggering_actor_id=123,
        repository=request.repository,
        head_repository=request.repository,
        head_branch="main",
        head_sha=request.workflow_sha,
        path=".github/workflows/merge_queue_validate.yml",
        event="workflow_dispatch",
        display_title=request.title,
        run_attempt=1,
        status="completed",
        conclusion="success",
    )
    repo = Mock(spec=RepoClient, full_name=request.repository)
    repo.branch_sha = AsyncMock(return_value=request.workflow_sha)
    repo.ref_sha = AsyncMock(return_value=request.candidate_sha)
    repo.validation_runs = AsyncMock(return_value=(run,))
    repo.validation_run = AsyncMock(return_value=run)
    repo.validation_jobs = AsyncMock(
        return_value=tuple(
            ValidationJob(
                id=index,
                run_id=42,
                run_attempt=1,
                name=name,
                status="completed",
                conclusion="success",
            )
            for index, name in enumerate(("candidate-smoke", "candidate-result"), 1)
        )
    )
    repo.validation_artifacts = AsyncMock(
        return_value=(Artifact(id=7, name=f"merge-queue-result-{attempt_id}-1", expired=False),)
    )
    repo.artifact_json = AsyncMock(
        return_value={
            **request.model_dump(mode="json"),
            "run_id": 42,
            "run_attempt": 1,
            "tested_sha": request.candidate_sha,
        }
    )
    return (
        CandidateValidation(
            queue,
            repo,
            QueuePolicy(checks={}, validation=ValidationPolicy(dispatch_actor_id=123)),
            Mock(spec=DispatchGateway, dispatch=AsyncMock()),
        ),
        work,
        Lease(uuid7(), 1),
    )


async def test_discovers_run_when_database_clock_is_ahead(
    validation: tuple[CandidateValidation, ValidationWork, Lease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, work, lease = validation
    assert work.attempt is not None
    github_created_at = work.attempt.created_at - timedelta(seconds=2)
    run = await controller.repo.validation_run(42)

    async def runs_since(_workflow: str, since: datetime) -> tuple[ValidationRun, ...]:
        return (run,) if since <= github_created_at else ()

    monkeypatch.setattr(controller.repo, "validation_runs", runs_since)
    await controller.reconcile(lease)
    finish = controller.queue.finish_validation
    assert isinstance(finish, AsyncMock) and finish.await_count == 1
    assert finish.call_args.kwargs == {"succeeded": True}
    assert finish.call_args.args[1]["artifact_id"] == 7


@pytest.mark.parametrize("status", [403, 404, 409, 410, 422])
async def test_definite_dispatch_rejection_records_failure_immediately(
    validation: tuple[CandidateValidation, ValidationWork, Lease],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    controller, work, lease = validation
    monkeypatch.setattr(
        controller.queue,
        "advance_validation",
        AsyncMock(
            side_effect=[
                ValidationWork(work.batch),
                replace(work, dispatch=True),
            ]
        ),
    )
    dispatch = AsyncMock(side_effect=DispatchRejected(status))
    monkeypatch.setattr(controller.dispatcher, "dispatch", dispatch)
    await controller.reconcile(lease)
    assert dispatch.await_count == 1
    assert work.attempt is not None and work.request is not None
    finish = controller.queue.finish_validation
    assert isinstance(finish, AsyncMock)
    finish.assert_awaited_once_with(
        work.attempt.id,
        {
            **work.request.model_dump(mode="json"),
            "error": "Dispatch gateway rejected the workflow dispatch",
            "http_status": status,
        },
        lease,
        succeeded=False,
    )


@pytest.mark.parametrize(
    "fault",
    [
        "workflow_revision",
        "wrong_actor",
        "rerun",
        "duplicate_run",
        "skipped_job",
        "missing_artifact",
        "wrong_candidate",
        "rerun_during_download",
    ],
)
async def test_invalid_evidence_cannot_pass_validation(
    validation: tuple[CandidateValidation, ValidationWork, Lease],
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, work, lease = validation
    run = await controller.repo.validation_run(42)
    if fault in {"workflow_revision", "wrong_actor", "rerun"}:
        changed = run.model_copy(
            update={
                "workflow_revision": {"head_sha": "e" * 40},
                "wrong_actor": {"triggering_actor_id": 456},
                "rerun": {"run_attempt": 2},
            }[fault]
        )
        monkeypatch.setattr(controller.repo, "validation_run", AsyncMock(return_value=changed))
    elif fault == "duplicate_run":
        monkeypatch.setattr(
            controller.repo,
            "validation_runs",
            AsyncMock(
                return_value=(
                    run,
                    run.model_copy(update={"id": 43}),
                )
            ),
        )
    elif fault == "skipped_job":
        jobs = await controller.repo.validation_jobs(42)
        monkeypatch.setattr(
            controller.repo,
            "validation_jobs",
            AsyncMock(
                return_value=(
                    jobs[0].model_copy(update={"conclusion": "skipped"}),
                    jobs[1],
                )
            ),
        )
    elif fault == "missing_artifact":
        monkeypatch.setattr(controller.repo, "validation_artifacts", AsyncMock(return_value=()))
    elif fault == "wrong_candidate":
        report = await controller.repo.artifact_json(7, "result.json")
        monkeypatch.setattr(
            controller.repo,
            "artifact_json",
            AsyncMock(
                return_value={
                    **report,
                    "tested_sha": "e" * 40,
                }
            ),
        )
    else:
        monkeypatch.setattr(
            controller.repo,
            "validation_run",
            AsyncMock(
                side_effect=[
                    run,
                    run.model_copy(update={"run_attempt": 2}),
                ]
            ),
        )
    await controller.reconcile(lease)
    finish = controller.queue.finish_validation
    assert isinstance(finish, AsyncMock) and finish.await_count == 1
    assert finish.call_args.kwargs == {"succeeded": False}
    assert work.request is not None
    assert finish.call_args.args[1]["candidate_sha"] == work.request.candidate_sha


async def test_base_change_discards_successful_results(
    validation: tuple[CandidateValidation, ValidationWork, Lease], monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, work, lease = validation
    monkeypatch.setattr(controller.repo, "branch_sha", AsyncMock(side_effect=["a" * 40, "e" * 40]))
    await controller.reconcile(lease)
    assert isinstance(controller.queue.invalidate, AsyncMock)
    controller.queue.invalidate.assert_awaited_once_with(work.batch.id, lease)
    assert isinstance(controller.queue.finish_validation, AsyncMock)
    controller.queue.finish_validation.assert_not_awaited()
