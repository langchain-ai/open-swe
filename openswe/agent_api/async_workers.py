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

From then on the worker is any other worker: its runs, the launched one included,
report through the completion webhook, so task messages, cancellation and result
delivery are the task service's. A launched run that ended before its worker was
registered is settled at registration instead. Settling is safe to repeat.
"""

import logging
from dataclasses import dataclass
from typing import Final
from uuid import uuid7

from langgraph.runtime import Runtime
from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, JsonValue, TypeAdapter
from runtime_api.endpoint import Handlers
from runtime_api.workers import (
    ADOPT,
    LAUNCH_WORKER,
    WORKER_LAUNCHED,
    Adopt,
    Adopted,
    Launch,
    Launched,
    LaunchReply,
    Worker,
)

from openswe.agent_api.client import remote_run_context
from openswe.agent_api.runs import in_graph_context, run_config
from openswe.agent_api.tokens import RemoteRun, WorkerGrant, sign_worker_grant
from openswe.dispatch import prepare_run_config
from openswe.prompts import prompt
from openswe.tasks import service, store

logger = logging.getLogger(__name__)

_TERMINAL: Final = frozenset({"success", "error", "timeout", "interrupted"})
_json_object = TypeAdapter(dict[str, JsonValue])
_INSTRUCTIONS_MARK: Final = "\x00instructions\x00"


class _Run(BaseModel):
    status: str
    metadata: JsonValue = None
    error: JsonValue = None

    @property
    def ended(self) -> bool:
        return self.status in _TERMINAL


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


async def launch(run: RemoteRun, body: Launch) -> LaunchReply:
    """A worker grant and assignment for the coordinator's launch, or the worker a replay made."""
    actor = await _actor(run)
    launched = await service.authorize_async_worker(actor, body.tool_call_id, body.instructions)
    if launched is not None:
        return LaunchReply(worker=_json_object.validate_python(launched))
    grant = WorkerGrant(coordinator=run, request_id=body.tool_call_id)
    return LaunchReply(
        grant=sign_worker_grant(grant),
        assignment=_assignment(run.thread_id, body.instructions.strip()),
    )


async def launched(run: RemoteRun, body: Launched) -> Worker:
    actor = await _actor(run)
    launched_run = LaunchedRun(body.worker_thread_id, body.worker_run_id)
    try:
        worker = await service.register_async_worker(
            actor, body.tool_call_id, body.worker_thread_id, body.instructions
        )
    except store.WorkerAlreadyLaunchedError as replay:
        # A replayed launch started this thread too: the coordinator keeps the first worker.
        await launched_run.cancel()
        worker = service.worker_reply(replay.task, replay.delegation)
    else:
        await launched_run.settle_if_ended()
    return Worker(worker=_json_object.validate_python(worker))


async def adopt(grant: WorkerGrant, body: Adopt) -> Adopted:
    """The worker's own run token, for the thread MDA launched from the grant's coordinator."""
    actor = await _actor(grant.coordinator)
    # Registered as the coordinator's worker_launched registers it, whichever comes first.
    instructions = _assigned_instructions(grant.coordinator.thread_id, body.instructions)
    await service.register_async_worker(actor, grant.request_id, body.thread_id, instructions)
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
    return Adopted(run_token=str(context["__run_token"]))


@dataclass(frozen=True)
class LaunchedRun:
    """A worker's first run, which MDA started with Open SWE's completion webhook."""

    thread_id: str
    run_id: str

    @property
    def log_extra(self) -> dict[str, str]:
        return {"worker_thread_id": self.thread_id, "run_id": self.run_id}

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

    async def settle_if_ended(self) -> None:
        """Settle a run that ended before its worker was registered, which its webhook could not."""
        # The webapp must not import the completion stack at startup.
        from openswe.completion import handle_run_completion
        from openswe.threads.runtimes import RUNTIMES

        try:
            run = _Run.model_validate(
                dict(await RUNTIMES.mda.client.runs.get(self.thread_id, self.run_id))
            )
        except NotFoundError:
            logger.warning("An async worker's launched run is gone", extra=self.log_extra)
            return
        if run.ended:
            await handle_run_completion(
                {
                    "thread_id": self.thread_id,
                    "run_id": self.run_id,
                    "status": run.status,
                    "metadata": run.metadata,
                    "error": run.error,
                }
            )


# What a coordinator's run calls to launch its workers.
RUN_API: Final = Handlers[RemoteRun]()
RUN_API.serve(LAUNCH_WORKER, launch)
RUN_API.serve(WORKER_LAUNCHED, launched)
# What a launched worker's grant may call, besides reading its coordinator's catalog.
GRANT_API: Final = Handlers[WorkerGrant]()
GRANT_API.serve(ADOPT, adopt)
