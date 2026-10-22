from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from openswe.github.http import RepoClient
from openswe.github.queue import QueuePull, Review, WorkflowPull, WorkflowRun
from openswe.merge_queue.admission import (
    AdmissionReader,
    AdmissionRejected,
    CheckSource,
    QueuePolicy,
)


@pytest.fixture
def repo() -> Mock:
    now = datetime.now(UTC)
    repo = Mock(spec=RepoClient)
    repo.full_name = "acme/widgets"
    snapshot = QueuePull(
        number=1,
        state="OPEN",
        merged=False,
        draft=False,
        head="1" * 40,
        base="a" * 40,
        branch="main",
        target="a" * 40,
        head_repository="acme/widgets",
        base_repository="acme/widgets",
        decision="APPROVED",
        reviews=(
            Review(id=10, state="APPROVED", updated_at=now - timedelta(minutes=2), commit="1" * 40),
        ),
    )
    pull = Mock()
    pull.queue_state = AsyncMock(return_value=snapshot)
    repo.pull_request.return_value = pull
    repo.branch = AsyncMock(
        return_value={
            "protection": {
                "required_status_checks": {
                    "contexts": ["approval"],
                    "checks": [{"context": "approval", "app_id": 100}],
                }
            }
        }
    )
    repo.branch_rules = AsyncMock(return_value=[])
    repo.commit_statuses = AsyncMock(return_value=[])
    repo.check_runs = AsyncMock(
        return_value=[
            {
                "id": 20,
                "name": "approval",
                "head_sha": "1" * 40,
                "status": "completed",
                "conclusion": "success",
                "started_at": now.isoformat(),
                "app": {"id": 100},
                "check_suite": {"id": 30},
            }
        ]
    )
    repo.merge_base = AsyncMock(return_value="a" * 40)
    repo.read_files = AsyncMock(return_value={".github/workflows/approval.yml": "trusted workflow"})
    repo.workflow_for_suite = AsyncMock(
        return_value=WorkflowRun(
            id=40,
            head_sha="1" * 40,
            path=".github/workflows/approval.yml",
            event="pull_request_review",
            run_attempt=2,
            check_suite_id=30,
            repository="acme/widgets",
            pull_requests=(WorkflowPull(number=1, head="1" * 40),),
        )
    )
    repo.admission_artifact = AsyncMock(
        return_value={
            "repository": "acme/widgets",
            "number": 1,
            "headRefOid": "1" * 40,
            "baseRefOid": "a" * 40,
        }
    )
    return repo


def reader(repo: RepoClient) -> AdmissionReader:
    return AdmissionReader(
        repo,
        "main",
        QueuePolicy(
            checks={
                "approval": CheckSource(
                    app_id=100,
                    workflow_path=".github/workflows/approval.yml",
                    review_artifact_prefix="review-admission",
                )
            }
        ),
    )


async def test_admission_binds_required_checks_and_reviews_to_current_revision(repo: Mock) -> None:
    admission = await reader(repo).check(1, "1" * 40, ())
    assert admission.head_sha == "1" * 40 and admission.base_sha == "a" * 40
    assert admission.snapshot_digest and admission.policy_digest
    # Re-running unchanged evidence preserves the snapshot identity.
    assert (await reader(repo).check(1, "1" * 40, ())).snapshot_digest == admission.snapshot_digest


@pytest.mark.parametrize(
    "changed",
    ["head", "approval", "source", "workflow", "review_freshness", "evidence", "during_read"],
)
async def test_untrusted_or_changed_admission_fails_closed(repo: Mock, changed: str) -> None:
    pull = repo.pull_request.return_value
    snapshot = pull.queue_state.return_value
    if changed == "head":
        pull.queue_state.return_value = snapshot.model_copy(update={"head": "2" * 40})
    elif changed == "approval":
        pull.queue_state.return_value = snapshot.model_copy(
            update={"decision": "CHANGES_REQUESTED"}
        )
    elif changed == "source":
        repo.check_runs.return_value[0]["app"] = {"id": 200}
    elif changed == "workflow":
        repo.read_files.side_effect = [
            {".github/workflows/approval.yml": "trusted workflow"},
            {".github/workflows/approval.yml": "changed workflow"},
        ]
    elif changed == "review_freshness":
        repo.check_runs.return_value[0]["started_at"] = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
    elif changed == "evidence":
        repo.admission_artifact.return_value["headRefOid"] = "2" * 40
    else:
        pull.queue_state.side_effect = [snapshot, snapshot.model_copy(update={"reviews": ()})]
    with pytest.raises(AdmissionRejected):
        await reader(repo).check(1, "1" * 40, ())
