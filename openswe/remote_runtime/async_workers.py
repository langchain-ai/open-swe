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
has no webhook, so this backend joins it and settles it as the webhook would.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
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
from openswe.tasks import service
from openswe.threads.status import refresh_thread_status

logger = logging.getLogger(__name__)

_TERMINAL: Final = frozenset({"success", "error", "timeout", "interrupted"})
# Past the platform's one-day limit on a background run.
_JOIN_SECONDS: Final = 25 * 60 * 60
_RETRY_SECONDS: Final = 30.0
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

    return await in_graph_context(run_config(run), resolve)


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
    worker = await service.register_async_worker(
        actor, body.tool_call_id, body.worker_thread_id, body.instructions
    )
    _join(body.worker_thread_id, body.run_id)
    return _json_object.validate_python({"worker": worker})


async def adopt(grant: WorkerGrant, raw: Mapping[str, JsonValue]) -> JsonValue:
    """The worker's own run token, for the thread MDA launched from the grant's coordinator."""
    body = AdoptBody.model_validate(raw)
    actor = await _actor(grant.coordinator)
    # Registered as the coordinator's worker_launched registers it, whichever comes first.
    instructions = _assigned_instructions(grant.coordinator.thread_id, body.instructions)
    await service.register_async_worker(actor, grant.request_id, body.thread_id, instructions)
    _join(body.thread_id, body.run_id)
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


def _join(thread_id: str, run_id: str) -> None:
    key = (thread_id, run_id)
    if key in _joining:
        return
    task = asyncio.create_task(_settle_when_done(thread_id, run_id))
    _joining[key] = task
    task.add_done_callback(lambda _: _joining.pop(key, None))


async def _settle_when_done(thread_id: str, run_id: str) -> None:
    """Settle the launched run as its completion webhook would, which MDA's launch never set."""
    # The webapp must not import the completion stack at startup.
    from openswe.completion import handle_run_completion
    from openswe.threads.runtimes import RUNTIMES

    client = RUNTIMES.mda.client
    deadline = time.monotonic() + _JOIN_SECONDS
    extra = {"worker_thread_id": thread_id, "run_id": run_id}
    payload: dict[str, JsonValue] | None = None
    while payload is None and time.monotonic() < deadline:
        try:
            run = _json_object.validate_python(dict(await client.runs.get(thread_id, run_id)))
            if run.get("status") in _TERMINAL:
                # This run's own checkpoint: a later run on the thread may have moved its state on.
                history = await client.threads.get_history(
                    thread_id, limit=1, metadata={"run_id": run_id}
                )
                checkpoint = _Checkpoint.model_validate(history[0]) if history else _Checkpoint()
                if run.get("status") == "success" and checkpoint.waiting:
                    # Not finished: the run that resumes it reports, through its own webhook.
                    logger.info(
                        "An async worker's launched run is waiting for a human", extra=extra
                    )
                    await refresh_thread_status(thread_id)
                    return
                payload = {
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "status": run.get("status"),
                    "metadata": run.get("metadata"),
                    "values": checkpoint.values,
                    "error": run.get("error"),
                }
                continue
            await client.runs.join(thread_id, run_id)
        except TimeoutError, httpx.TimeoutException:
            # A long run outlasts one long-poll; ask again.
            logger.debug("An async worker's launched run is still going", extra=extra)
        except NotFoundError:
            logger.warning("An async worker's launched run is gone", extra=extra)
            return
        except Exception:
            logger.warning(
                "Could not check an async worker's launched run; retrying",
                extra=extra,
                exc_info=True,
            )
            await asyncio.sleep(_RETRY_SECONDS)
    if payload is None:
        logger.warning("Stopped waiting for an async worker's launched run", extra=extra)
        return
    # Once, as the completion webhook delivers it: settling is not safe to repeat.
    try:
        await handle_run_completion(payload)
    except Exception:
        logger.exception("Could not settle an async worker's launched run", extra=extra)
