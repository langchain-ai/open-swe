"""Task workers launched as MDA async subagents, made ordinary Open SWE workers.

The coordinator's MDA run launches the worker itself, with deepagents'
``start_async_task``, as a run of the ``open-swe--general-purpose`` graph on a
new thread. That launch forwards the coordinator's run context, so it would run
the worker as the coordinator. Instead:

- ``launch_worker``: the coordinator's run asks for a worker grant, which the
  launch carries in place of its run token. It is refused wherever the thread may
  not delegate: a worker can't launch another.
- ``worker_launched``: once the thread exists, the coordinator registers it as a
  worker of its task, with its own index entry, owner and sandbox access.
- ``adopt``: the worker's first run exchanges the grant and its thread id for a run
  token of its own; it registers itself if the coordinator has not yet.

From then on the worker is any other worker: its runs go through the thread
router with their own tokens and the completion webhook, so task messages,
cancellation and result delivery are the task service's. Only the launched run
has no webhook, so this backend settles it as the webhook would: the process
that registered the worker joins the run, and the thread-status reconcile job
settles any launched run a restart left unjoined. Settling is safe to repeat.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid7

import httpx
from langgraph.runtime import Runtime
from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe.dispatch import prepare_run_config
from openswe.prompts import prompt
from openswe.remote_runtime.client import remote_run_context
from openswe.remote_runtime.runs import in_graph_context, run_config
from openswe.remote_runtime.tokens import RemoteRun, WorkerGrant, sign_worker_grant
from openswe.tasks import service, store
from openswe.threads.status import refresh_thread_status

logger = logging.getLogger(__name__)

_TERMINAL: Final = frozenset({"success", "error", "timeout", "interrupted"})
# Past the platform's one-day limit on a background run.
_HORIZON: Final = timedelta(hours=25)
_RETRY_SECONDS: Final = 30.0
_RECONCILE_BATCH: Final = 100
_json_object = TypeAdapter(dict[str, JsonValue])
_INSTRUCTIONS_MARK: Final = "\x00instructions\x00"
# Joins this process is running, so a retried hook never joins a run twice.
_joining: dict[tuple[str, str], asyncio.Task[None]] = {}


class LaunchBody(BaseModel):
    tool_call_id: str
    instructions: str


class LaunchedBody(LaunchBody):
    worker_thread_id: str
    run_id: str


class _Run(BaseModel):
    status: str
    metadata: JsonValue = None
    error: JsonValue = None
    created_at: datetime | None = None

    @property
    def ended(self) -> bool:
        return self.status in _TERMINAL


class _Task(BaseModel):
    interrupts: list[JsonValue] = []


class _Checkpoint(BaseModel):
    """A run's own last checkpoint, as the thread's history returns it."""

    values: dict[str, JsonValue] | None = None
    interrupts: list[JsonValue] = []
    tasks: list[_Task] = []

    @property
    def waiting(self) -> bool:
        """Paused on ``interrupt()``: its run ends as ``success``, only the checkpoint shows the pause."""
        return bool(self.interrupts) or any(task.interrupts for task in self.tasks)


class AdoptBody(BaseModel):
    thread_id: str
    run_id: str
    # The assignment the worker was launched with, its first message.
    instructions: str


def _assignment(coordinator_thread_id: str, instructions: str) -> str:
    """What a launched worker is first told: its coordinator and the coordinator's instructions."""
    return prompt(
        "tasks/assignment", coordinator_thread_id=coordinator_thread_id, instructions=instructions
    )


def _assigned_instructions(coordinator_thread_id: str, assignment: str) -> str:
    """The coordinator's instructions inside the assignment ``launch`` rendered for them."""
    before, _, after = _assignment(coordinator_thread_id, _INSTRUCTIONS_MARK).partition(
        _INSTRUCTIONS_MARK
    )
    text = assignment.strip()
    if text.startswith(before.lstrip()) and text.endswith(after.rstrip()):
        return text[len(before.lstrip()) : len(text) - len(after.rstrip())].strip() or text
    logger.warning(
        "An async worker's assignment does not match its template",
        extra={"coordinator_thread_id": coordinator_thread_id},
    )
    return text


async def _actor(run: RemoteRun) -> service.Actor:
    """Who the run acts for, resolved as the run's own tools resolve it."""

    async def resolve(runtime: Runtime) -> service.Actor:  # noqa: ARG001
        return await service.Actor.resolve({})

    return await in_graph_context({**run_config(run), "run_name": "worker.actor"}, resolve)


async def launch(run: RemoteRun, raw: Mapping[str, JsonValue]) -> JsonValue:
    """A worker grant and assignment for the coordinator's launch, or the worker a replay made."""
    body = LaunchBody.model_validate(raw)
    actor = await _actor(run)
    launched = await service.authorize_async_worker(actor, body.tool_call_id, body.instructions)
    if launched is not None:
        return _json_object.validate_python({"worker": launched})
    grant = WorkerGrant(coordinator=run, request_id=body.tool_call_id)
    return {
        "grant": sign_worker_grant(grant),
        "assignment": _assignment(run.thread_id, body.instructions.strip()),
    }


async def launched(run: RemoteRun, raw: Mapping[str, JsonValue]) -> JsonValue:
    body = LaunchedBody.model_validate(raw)
    actor = await _actor(run)
    launched_run = LaunchedRun(body.worker_thread_id, body.run_id)
    try:
        worker = await service.register_async_worker(
            actor, body.tool_call_id, body.worker_thread_id, body.instructions, body.run_id
        )
    except store.WorkerAlreadyLaunchedError as replay:
        # A replayed launch started this thread too: the coordinator keeps the first worker.
        await launched_run.cancel()
        worker = service.worker_reply(replay.task, replay.delegation)
    else:
        launched_run.join()
    return _json_object.validate_python({"worker": worker})


async def adopt(grant: WorkerGrant, raw: Mapping[str, JsonValue]) -> JsonValue:
    """The worker's own run token, for the thread MDA launched from the grant's coordinator."""
    body = AdoptBody.model_validate(raw)
    actor = await _actor(grant.coordinator)
    # Registered as the coordinator's worker_launched registers it, whichever comes first.
    instructions = _assigned_instructions(grant.coordinator.thread_id, body.instructions)
    await service.register_async_worker(
        actor, grant.request_id, body.thread_id, instructions, body.run_id
    )
    LaunchedRun(body.thread_id, body.run_id).join()
    configurable = prepare_run_config(
        {
            "configurable": {
                **await service.recipient_config(body.thread_id),
                "transcript_turn_id": str(uuid7()),
            }
        },
        None,
    )["configurable"]
    context = await remote_run_context(
        _json_object.validate_python(configurable),
        # MDA started this run, not a task message: no run metadata comes with it.
        metadata={},
        thread_id=body.thread_id,
        assistant_id=grant.coordinator.assistant_id,
    )
    return {"run_token": context["__run_token"]}


@dataclass(frozen=True)
class LaunchedRun:
    """A worker's first run, which MDA started without Open SWE's completion webhook."""

    thread_id: str
    run_id: str

    @property
    def log_extra(self) -> dict[str, str]:
        return {"worker_thread_id": self.thread_id, "run_id": self.run_id}

    def join(self) -> None:
        """Settle the run once it ends, from this process; the reconcile job covers a restart."""
        key = (self.thread_id, self.run_id)
        if key in _joining:
            return
        task = asyncio.create_task(self.settle_when_done())
        _joining[key] = task
        task.add_done_callback(lambda _: _joining.pop(key, None))

    async def cancel(self) -> None:
        from openswe.threads.runtimes import RUNTIMES

        try:
            await RUNTIMES.mda.client.runs.cancel(self.thread_id, self.run_id)
        except Exception:
            logger.warning(
                "Could not cancel a replayed launch's second worker run",
                extra=self.log_extra,
                exc_info=True,
            )

    async def fetch(self) -> _Run | None:
        """The run as MDA reports it, or ``None`` once MDA no longer has it."""
        from openswe.threads.runtimes import RUNTIMES

        try:
            run = await RUNTIMES.mda.client.runs.get(self.thread_id, self.run_id)
        except NotFoundError:
            return None
        return _Run.model_validate(dict(run))

    async def settle(self, run: _Run | None) -> None:
        """Settle the ended run as its completion webhook would, then mark it settled."""
        # The webapp must not import the completion stack at startup.
        from openswe.completion import handle_run_completion
        from openswe.threads.runtimes import RUNTIMES

        if run is None:
            logger.warning("An async worker's launched run is gone", extra=self.log_extra)
        else:
            # This run's own checkpoint: a later run on the thread may have moved its state on.
            history = await RUNTIMES.mda.client.threads.get_history(
                self.thread_id, limit=1, metadata={"run_id": self.run_id}
            )
            checkpoint = _Checkpoint.model_validate(history[0]) if history else _Checkpoint()
            if run.status == "success" and checkpoint.waiting:
                # Not finished: the run that resumes it reports, through its own webhook.
                logger.info(
                    "An async worker's launched run is waiting for a human", extra=self.log_extra
                )
                await refresh_thread_status(self.thread_id)
            else:
                await handle_run_completion(
                    {
                        "thread_id": self.thread_id,
                        "run_id": self.run_id,
                        "status": run.status,
                        "metadata": run.metadata,
                        "values": checkpoint.values,
                        "error": run.error,
                    }
                )
        await store.TaskDelegation.settle_launch(self.thread_id, self.run_id)

    async def _wait(self) -> _Run | None:
        """The run once it ends, or ``None`` once MDA no longer has it."""
        from openswe.threads.runtimes import RUNTIMES

        deadline = time.monotonic() + _HORIZON.total_seconds()
        while time.monotonic() < deadline:
            try:
                run = await self.fetch()
                if run is None or run.ended:
                    return run
                await RUNTIMES.mda.client.runs.join(self.thread_id, self.run_id)
            except TimeoutError, httpx.TimeoutException:
                # A long run outlasts one long-poll; ask again.
                logger.debug("An async worker's launched run is still going", extra=self.log_extra)
            except Exception:
                logger.warning(
                    "Could not check an async worker's launched run; retrying",
                    extra=self.log_extra,
                    exc_info=True,
                )
                await asyncio.sleep(_RETRY_SECONDS)
        raise TimeoutError

    async def settle_when_done(self) -> None:
        try:
            run = await self._wait()
        except TimeoutError:
            logger.warning(
                "Stopped waiting for an async worker's launched run", extra=self.log_extra
            )
            return
        try:
            await self.settle(run)
        except Exception:
            logger.exception(
                "Could not settle an async worker's launched run; the reconcile job retries it",
                extra=self.log_extra,
            )


async def settle_launched_runs() -> int:
    """Settle workers' launched runs that ended with no process joining them, as after a restart."""
    settled = 0
    horizon = datetime.now(UTC) - _HORIZON
    for thread_id, run_id in await store.TaskDelegation.unsettled_launches(_RECONCILE_BATCH):
        if (thread_id, run_id) in _joining:
            continue
        launched = LaunchedRun(thread_id, run_id)
        try:
            run = await launched.fetch()
            if run is not None and run.created_at is not None and run.created_at < horizon:
                # Long past the platform's limit on a run: settling it has kept failing.
                logger.warning(
                    "Gave up settling an async worker's launched run", extra=launched.log_extra
                )
                await store.TaskDelegation.settle_launch(thread_id, run_id)
                continue
            if run is not None and not run.ended:
                continue
            await launched.settle(run)
        except Exception:
            logger.warning(
                "Could not settle an async worker's launched run",
                extra=launched.log_extra,
                exc_info=True,
            )
            continue
        settled += 1
    return settled
