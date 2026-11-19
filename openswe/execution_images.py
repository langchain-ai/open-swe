"""Queue-backed builds and immutable publication of Robin's global execution image."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.database import postgres
from openswe.jobs import queue
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import JsonObject
from openswe.utils.time import now_iso, parse_iso
from openswe.workspaces import refresh
from openswe.workspaces.store import (
    REFRESH_LOG_MAX_CHARS,
    REFRESH_LOG_TAIL_CHARS,
    RefreshKind,
    RefreshStatus,
    RefreshStep,
    StepStatus,
    Workspace,
    capture_sandbox_snapshot,
    snapshot_name_prefix,
)

logger = logging.getLogger(__name__)


class ExecutionTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["workspace", "global"]
    workspace_slug: str | None = None

    @model_validator(mode="after")
    def valid_workspace(self) -> Self:
        if (self.kind == "workspace" and not (self.workspace_slug or "").strip()) or (
            self.kind == "global" and self.workspace_slug is not None
        ):
            raise ValueError("workspace_slug is required only for workspace execution targets")
        return self


class ImageState(BaseModel):
    snapshot_id: str | None = None
    last_captured_at: str | None = None
    refresh_status: RefreshStatus = "never"
    refresh_kind: RefreshKind | None = None
    refresh_run_id: str | None = None
    refresh_started_at: str | None = None
    refresh_finished_at: str | None = None
    refresh_steps: list[RefreshStep] = Field(default_factory=list)
    refresh_error: str | None = None
    refresh_log: str | None = None
    refresh_sandbox_id: str | None = None

    def captured_at(self, snapshot_id: str | None) -> str | None:
        return self.last_captured_at if snapshot_id and self.snapshot_id == snapshot_id else None

    def stale(self, definition: ExecutionDefaults) -> bool:
        if not definition.snapshot_id or not definition.update_script:
            return False
        captured = parse_iso(self.captured_at(definition.snapshot_id))
        return (
            captured is None
            or (datetime.now(UTC) - captured).total_seconds() >= refresh.UPDATE_INTERVAL_SECONDS
        )

    def update_due(self, definition: ExecutionDefaults) -> bool:
        if not self.stale(definition):
            return False
        finished = parse_iso(self.refresh_finished_at)
        return (
            finished is None
            or (datetime.now(UTC) - finished).total_seconds() >= refresh.UPDATE_INTERVAL_SECONDS
        )


class ExecutionImageAttempt:
    def __init__(
        self, images: ExecutionImages, job: queue.Job, definition: ExecutionDefaults
    ) -> None:
        self.images = images
        self.job = job
        self.definition = definition.model_copy(deep=True)
        self.fingerprint = images.fingerprint(definition)

    async def start_step(self, label: str, *, log_path: str | None = None) -> None:
        async with postgres.session() as session:
            state = await self.images.lock_state(session=session)
            if not await self.owns(state, session=session):
                raise ValueError("execution image job no longer owns its queue claim")
            state.refresh_steps = [
                *(step for step in state.refresh_steps if step.label != label),
                RefreshStep(label=label, started_at=now_iso(), log_path=log_path),
            ]
            await self.images.save_state(state, session=session)

    async def finish_step(
        self, label: str, status: StepStatus, *, exit_code: int | None = None
    ) -> None:
        async with postgres.session() as session:
            state = await self.images.lock_state(session=session)
            if not await self.owns(state, session=session):
                return
            state.refresh_steps = [
                step.model_copy(
                    update={"status": status, "finished_at": now_iso(), "exit_code": exit_code}
                )
                if step.label == label
                else step
                for step in state.refresh_steps
            ]
            await self.images.save_state(state, session=session)

    async def publish_builder(self, sandbox_id: str) -> None:
        async with postgres.session() as session:
            state = await self.images.lock_state(session=session)
            if not await self.owns(state, session=session):
                raise ValueError("execution image job no longer owns its queue claim")
            state.refresh_sandbox_id = sandbox_id
            await self.images.save_state(state, session=session)

    async def owns(self, state: ImageState, *, session: AsyncSession) -> bool:
        return state.refresh_run_id == str(self.job.id) and await self.job.held(session=session)

    async def settle(
        self, status: RefreshStatus, *, log: str | None = None, error: str | None = None
    ) -> None:
        async with postgres.session() as session:
            state = await self.images.lock_state(session=session)
            if not await self.owns(state, session=session):
                return
            state.refresh_status = status
            state.refresh_finished_at = now_iso()
            state.refresh_log = (log or "")[-REFRESH_LOG_MAX_CHARS:] or None
            state.refresh_error = error[:1000] if error else None
            state.refresh_sandbox_id = None
            state.refresh_steps = [
                step.model_copy(update={"status": "failed", "finished_at": now_iso()})
                if step.status == "running"
                else step
                for step in state.refresh_steps
            ]
            await self.images.save_state(state, session=session)

    async def capture(self, sandbox_id: str) -> None:
        name = self.definition.snapshot_name or f"{snapshot_name_prefix()}-execution-global"
        snapshot_id = await capture_sandbox_snapshot(
            sandbox_id, name, timeout=refresh.capture_timeout()
        )
        async with postgres.session() as session:
            current = ExecutionDefaults.model_validate(
                await self.images.records.lock(session=session)
            )
            state = ImageState.model_validate(
                await self.images.records.lock("image", session=session)
            )
            if not await self.owns(state, session=session):
                raise ValueError("execution image job lost its queue claim before publication")
            if self.images.fingerprint(current) != self.fingerprint:
                raise ValueError(
                    "execution image definition changed during the build; image retained"
                )
            await self.images.records.patch(
                "", {"snapshot_id": snapshot_id, "snapshot_name": name}, session=session
            )
            state.snapshot_id = snapshot_id
            state.last_captured_at = now_iso()
            await self.images.save_state(state, session=session)
        await Topic.WORKSPACES.invalidate()


class ExecutionImages:
    JOB = "execution_image_refresh"
    KEY = "execution_image_refresh:global"
    CRON_KEY = "execution_image_refresh:global:daily"
    TASK_PREFIX = "image-"

    def __init__(self) -> None:
        self.records = EXECUTION_DEFAULTS.records

    @staticmethod
    def fingerprint(definition: ExecutionDefaults) -> str:
        value = definition.model_dump(mode="json", exclude={"enabled", "prompt"})
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    async def state(self, *, session: AsyncSession | None = None) -> ImageState:
        return ImageState.model_validate(await self.records.get("image", session=session) or {})

    async def lock_state(self, *, session: AsyncSession) -> ImageState:
        await self.records.lock(session=session)
        return ImageState.model_validate(await self.records.lock("image", session=session))

    async def save_state(self, state: ImageState, *, session: AsyncSession) -> None:
        await self.records.patch("image", state.model_dump(mode="json"), session=session)

    async def ensure_refresh_cron(self) -> str | None:
        definition = await EXECUTION_DEFAULTS.load()
        if not definition.enabled or not definition.setup_script:
            return None
        job = await queue.schedule(
            self.JOB,
            {"target": {"kind": "global"}, "refresh_kind": "full"},
            cron=refresh.daily_schedule("global-execution"),
            key=self.CRON_KEY,
            metadata={"kind": self.JOB, "target": "global"},
            replace=False,
        )
        return str(job.id)

    @staticmethod
    def assert_refreshable(definition: ExecutionDefaults, kind: RefreshKind) -> None:
        if not definition.enabled:
            raise ValueError(
                "global execution defaults are disabled; enable Use Robin-wide Defaults before rebuilding"
            )
        if kind == "full" and not definition.setup_script:
            raise ValueError("global execution image has no setup script to run")
        if kind == "update" and not definition.update_script:
            raise ValueError("global execution image has no update script to run")
        if kind == "update" and not definition.snapshot_id:
            raise ValueError("global execution image has no snapshot to update")

    async def start(self, kind: RefreshKind = "full") -> str:
        definition = await EXECUTION_DEFAULTS.load()
        self.assert_refreshable(definition, kind)
        return await self.enqueue(kind)

    async def enqueue(self, kind: RefreshKind, *, sandbox_id: str | None = None) -> str:
        payload: JsonObject = {
            "target": ExecutionTarget(kind="global").model_dump(mode="json"),
            "refresh_kind": kind,
        }
        if sandbox_id:
            payload["source_sandbox_id"] = sandbox_id
        job = await queue.schedule(
            self.JOB,
            payload,
            key=self.KEY,
            metadata={"kind": self.JOB, "target": "global", "refresh_kind": kind},
            replace=False,
        )
        if sandbox_id is not None and job.payload.get("source_sandbox_id") != sandbox_id:
            raise ValueError(
                "another global image refresh is queued; finish or stop it before publishing"
            )
        return str(job.id)

    async def publish(self, sandbox_id: str) -> str:
        if not await EXECUTION_DEFAULTS.is_enabled():
            raise ValueError(
                "global execution defaults are disabled; enable Use Robin-wide Defaults before publishing"
            )
        return await self.enqueue("full", sandbox_id=sandbox_id)

    async def maybe_start_update(self) -> str | None:
        try:
            definition = await EXECUTION_DEFAULTS.load()
            if not definition.enabled:
                return None
            await self.ensure_refresh_cron()
            if not (await self.state()).update_due(definition):
                return None
            return await self.start("update")
        except Exception:
            logger.warning("Could not start global execution image update", exc_info=True)
            return None

    async def begin(self, job: queue.Job) -> ExecutionImageAttempt | None:
        interrupted_builder: str | None = None
        async with postgres.session() as session:
            definition = ExecutionDefaults.model_validate(await self.records.lock(session=session))
            state = ImageState.model_validate(await self.records.lock("image", session=session))
            if not definition.enabled or not await job.held(session=session):
                return None
            if "source_sandbox_id" not in job.payload:
                self.assert_refreshable(
                    definition, "update" if job.payload.get("refresh_kind") == "update" else "full"
                )
            interrupted_builder = state.refresh_sandbox_id
            state.refresh_status = "refreshing"
            state.refresh_kind = "update" if job.payload.get("refresh_kind") == "update" else "full"
            state.refresh_run_id = str(job.id)
            state.refresh_started_at = now_iso()
            state.refresh_finished_at = None
            state.refresh_steps = []
            state.refresh_log = None
            state.refresh_error = None
            state.refresh_sandbox_id = None
            await self.save_state(state, session=session)
        if interrupted_builder and interrupted_builder != job.payload.get("source_sandbox_id"):
            await refresh.release_builder_sandbox(interrupted_builder)
        return ExecutionImageAttempt(self, job, definition)

    async def refresh(self, job: queue.Job) -> dict[str, JsonValue]:
        target = ExecutionTarget.model_validate(job.payload.get("target"))
        if target.kind != "global":
            raise ValueError("execution image jobs require an explicit global target")
        definition = await EXECUTION_DEFAULTS.load()
        if not definition.enabled:
            return {"status": "global_execution_disabled"}
        if job.cron:
            if not definition.setup_script:
                return {"status": "no_setup_script"}
            return {"status": "queued", "run_id": await self.start("full")}
        attempt = await self.begin(job)
        if attempt is None:
            return {"status": "not_claimed"}
        source = job.payload.get("source_sandbox_id")
        if isinstance(source, str) and source:
            try:
                await attempt.start_step("capture")
                await attempt.capture(source)
                await attempt.finish_step("capture", "success")
                await attempt.settle("success")
                return {"status": "success", "target": "global"}
            except asyncio.CancelledError:
                await attempt.settle("failed", error="interrupted")
                raise
            except Exception as exc:
                logger.warning("Global execution image publication failed", exc_info=True)
                await attempt.settle("failed", error=str(exc) or type(exc).__name__)
                return {"status": "failed", "target": "global"}
        kind: RefreshKind = "update" if job.payload.get("refresh_kind") == "update" else "full"
        definition = attempt.definition
        adapter = Workspace(
            slug="global-execution",
            **definition.model_dump(exclude={"enabled", "prompt"}),
        )
        adapter.sandbox_create_params()
        build = refresh.SnapshotBuild(
            adapter,
            definition.snapshot_id if kind == "update" else definition.base_snapshot_id,
            kind,
            attempt,
            attempt.capture,
        )
        return {"target": "global", **await build.run()}

    async def status(self, *, with_output: bool = True) -> dict[str, JsonValue]:
        definition = await EXECUTION_DEFAULTS.load()
        state = await self.state()
        step = next((item for item in state.refresh_steps if item.status == "running"), None)
        result: dict[str, JsonValue] = {
            "target": ExecutionTarget(kind="global").model_dump(mode="json"),
            "task_id": self.TASK_PREFIX + state.refresh_run_id if state.refresh_run_id else None,
            "kind": self.JOB,
            "enabled": definition.enabled,
            "snapshot_id": definition.snapshot_id,
            "snapshot_name": definition.snapshot_name,
            "base_snapshot_id": definition.base_snapshot_id,
            "last_captured_at": state.captured_at(definition.snapshot_id),
            "status": {"refreshing": "running", "success": "completed"}.get(
                state.refresh_status, state.refresh_status
            ),
            "refresh_kind": state.refresh_kind,
            "started_at": state.refresh_started_at,
            "finished_at": state.refresh_finished_at,
            "step": step.label if step else None,
            "steps": [item.model_dump(mode="json") for item in state.refresh_steps],
            "error": state.refresh_error,
        }
        queued = await queue.get(self.KEY)
        if queued is not None and state.refresh_run_id != str(queued.id):
            return {
                **result,
                "task_id": self.TASK_PREFIX + str(queued.id),
                "status": "queued",
                "refresh_kind": queued.payload.get("refresh_kind"),
                "started_at": None,
                "finished_at": None,
                "steps": [],
                "step": None,
                "error": None,
            }
        if with_output:
            result["output"] = (state.refresh_log or "")[-REFRESH_LOG_TAIL_CHARS:] or None
            result["output_source"] = "record"
            if step and step.log_path and state.refresh_sandbox_id:
                result["output"] = await refresh.read_builder_log(
                    state.refresh_sandbox_id, step.log_path
                )
                result["output_source"] = "builder"
                result["output_path"] = step.log_path
                result["output_truncated"] = True
        return result

    async def task_status(self, task_id: str, *, with_output: bool = True) -> dict[str, JsonValue]:
        state = await self.state()
        run_id = task_id.removeprefix(self.TASK_PREFIX)
        if state.refresh_run_id == run_id:
            return await self.status(with_output=with_output)
        job = await queue.get(self.KEY)
        if job and str(job.id) == run_id:
            return {"task_id": task_id, "kind": self.JOB, "status": "queued", "target": "global"}
        return {"error": "task not found", "task_id": task_id}

    async def task_stop(self, task_id: str) -> dict[str, JsonValue]:
        state = await self.state()
        job = await queue.get(self.KEY)
        run_id = task_id.removeprefix(self.TASK_PREFIX)
        if job is None or str(job.id) != run_id:
            return await self.task_status(task_id)
        await queue.cancel(job.id)
        builder: str | None = None
        async with postgres.session() as session:
            state = await self.lock_state(session=session)
            if state.refresh_run_id == run_id:
                builder = state.refresh_sandbox_id
                state.refresh_status = "failed"
                state.refresh_finished_at = now_iso()
                state.refresh_error = "cancelled"
                state.refresh_sandbox_id = None
                await self.save_state(state, session=session)
        if builder:
            await refresh.release_builder_sandbox(builder)
        return {"task_id": task_id, "status": "cancelled", "target": "global"}


EXECUTION_IMAGES = ExecutionImages()
