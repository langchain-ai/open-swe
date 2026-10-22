"""Reconcile saved CI intent using GitHub's authenticated run, job and artifact APIs."""

from datetime import datetime, timedelta

from pydantic import JsonValue

from openswe.github.http import GitHubError, RepoClient
from openswe.github.queue import ValidationRun
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.git import Candidate, candidate_ref
from openswe.merge_queue.models import Batch, Lease
from openswe.merge_queue.store import MergeQueue
from openswe.merge_queue.validation_contract import WORKFLOW, ValidationRequest


class InvalidValidation(ValueError):
    pass


class CandidateValidation:
    def __init__(self, queue: MergeQueue, repo: RepoClient, policy: QueuePolicy):
        self.queue, self.repo, self.policy = queue, repo, policy

    async def reconcile(self, lease: Lease) -> None:
        if (await self.queue.repository_name(lease)).lower() != self.repo.full_name.lower():
            raise ValueError("GitHub client does not belong to this queue")
        work = await self.queue.advance_validation(self.policy, lease)
        if work is None:
            return
        candidate = Candidate.model_validate(work.batch.candidate)
        if await self._invalidate_if_candidate_changed(work.batch, candidate, lease):
            return
        if work.attempt is None:
            work = await self.queue.advance_validation(self.policy, lease, start=work.batch.id)
            if work is None:
                return
        attempt, request = work.attempt, work.request
        assert attempt is not None and request is not None
        if work.dispatch:
            await self._dispatch(request, lease)
            return
        try:
            run_id = await self._find_run(request, attempt.created_at, work.run_id)
            if run_id is None:
                return
            run = await self.repo.validation_run(run_id)
            self._validate_run_identity(run, request, run_id)
            await self.queue.bind_validation_run(attempt.id, run_id, lease)
            if run.status != "completed":
                return
            if run.conclusion != "success":
                raise InvalidValidation("Validation workflow did not succeed")
            evidence = await self._collect_evidence(request, run_id)
            latest = await self.repo.validation_run(run_id)
            self._validate_run_identity(latest, request, run_id)
            if latest.status != "completed" or latest.conclusion != "success":
                raise InvalidValidation("Workflow changed while collecting results")
            if await self._invalidate_if_candidate_changed(work.batch, candidate, lease):
                return
        except InvalidValidation as exc:
            await self.queue.finish_validation(
                attempt.id,
                {**request.model_dump(mode="json"), "error": str(exc)},
                lease,
                succeeded=False,
            )
            return
        await self.queue.finish_validation(attempt.id, evidence, lease, succeeded=True)

    async def _invalidate_if_candidate_changed(
        self, batch: Batch, candidate: Candidate, lease: Lease
    ) -> bool:
        if (
            await self.repo.branch_sha("main") != batch.base_sha
            or await self.repo.ref_sha(candidate_ref(batch.id)) != candidate.sha
        ):
            await self.queue.invalidate(batch.id, lease)
            return True
        return False

    async def _dispatch(self, request: ValidationRequest, lease: Lease) -> None:
        try:
            await self.repo.dispatch_validation(WORKFLOW, request.model_dump_json())
        except GitHubError as exc:
            status = exc.response.status_code
            if status not in {400, 401, 403, 404, 405, 410, 422, 429, 503}:
                raise
            await self.queue.finish_validation(
                request.attempt_id,
                {
                    **request.model_dump(mode="json"),
                    "error": "GitHub rejected the workflow dispatch",
                    "http_status": status,
                },
                lease,
                succeeded=False,
            )

    async def _find_run(
        self, request: ValidationRequest, created_at: datetime, bound_run_id: int | None
    ) -> int | None:
        # Search even after binding: a second matching run makes the attempt ambiguous.
        # Allow for skew between the database and GitHub clocks.
        runs = await self.repo.validation_runs(WORKFLOW, created_at - timedelta(minutes=5))
        matching_run_ids = {
            run.id
            for run in runs
            if run.display_title == request.title
            and run.actor_id == run.triggering_actor_id == request.dispatch_actor_id
        }
        if len(matching_run_ids) > 1:
            raise InvalidValidation("Multiple workflow runs match this attempt")
        run_id = next(iter(matching_run_ids), bound_run_id)
        if bound_run_id is not None and bound_run_id != run_id:
            raise InvalidValidation("Validation run changed")
        return run_id

    @staticmethod
    def _validate_run_identity(run: ValidationRun, request: ValidationRequest, run_id: int) -> None:
        if (
            run.id != run_id
            or run.actor_id != request.dispatch_actor_id
            or run.triggering_actor_id != request.dispatch_actor_id
            or run.repository != request.repository
            or run.head_repository != request.repository
            or run.head_branch != "main"
            or run.head_sha != request.workflow_sha
            or run.path.split("@", 1)[0] != f".github/workflows/{WORKFLOW}"
            or run.event != "workflow_dispatch"
            or run.display_title != request.title
            or run.run_attempt != 1
        ):
            raise InvalidValidation("Workflow identity does not match the validation request")

    async def _collect_evidence(
        self, request: ValidationRequest, run_id: int
    ) -> dict[str, JsonValue]:
        jobs = await self.repo.validation_jobs(run_id)
        if (
            sorted(job.name for job in jobs) != sorted([*request.expected_jobs, "candidate-result"])
            or len({job.id for job in jobs}) != len(jobs)
            or any(
                job.run_id != run_id
                or job.run_attempt != 1
                or job.status != "completed"
                or job.conclusion != "success"
                for job in jobs
            )
        ):
            raise InvalidValidation("Required validation jobs did not all succeed")
        matches = [
            artifact
            for artifact in await self.repo.validation_artifacts(run_id)
            if artifact.name == f"merge-queue-result-{request.attempt_id}-1"
        ]
        if len(matches) != 1 or matches[0].expired:
            raise InvalidValidation("Missing or ambiguous validation artifact")
        artifact = matches[0]
        expected: dict[str, JsonValue] = {
            **request.model_dump(mode="json"),
            "run_id": run_id,
            "run_attempt": 1,
            "tested_sha": request.candidate_sha,
        }
        if await self.repo.artifact_json(artifact.id, "result.json") != expected:
            raise InvalidValidation("Validation artifact does not match the candidate and attempt")
        return {**expected, "artifact_id": artifact.id, "job_ids": [job.id for job in jobs]}
