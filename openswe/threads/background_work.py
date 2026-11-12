"""The sandbox commands, monitors and event listeners keeping a thread busy, for the dashboard."""

import logging
from typing import TYPE_CHECKING, Self

from fastapi import HTTPException
from pydantic import BaseModel

from openswe.monitors.sandbox import ID_PREFIX, MonitorError, MonitorState, SandboxMonitors
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.diffs import connect_sandbox
from openswe.threads.summary import _assert_thread_promptable
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY
from openswe.webhooks.event_subscriptions import EventSubscription

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

OUTPUT_LINES = 200


class BackgroundCommand(BaseModel):
    task_id: str
    command: str | None = None
    status: str
    started_at: float | None = None


class EventListener(BaseModel):
    """An event subscription that wakes the thread when a matching event arrives."""

    subscription_id: str
    target: str
    sources: list[str]
    event_types: list[str]
    instructions: str
    one_shot: bool
    trigger_count: int
    last_triggered_at: float | None
    expires_at: float
    created_at: float | None

    @classmethod
    def of(cls, subscription: EventSubscription) -> Self:
        return cls(
            subscription_id=str(subscription.id),
            target=subscription.target,
            sources=subscription.sources,
            event_types=subscription.event_types,
            instructions=subscription.instructions,
            one_shot=subscription.one_shot,
            trigger_count=subscription.trigger_count,
            last_triggered_at=subscription.last_triggered_at.timestamp()
            if subscription.last_triggered_at
            else None,
            expires_at=subscription.expires_at.timestamp(),
            created_at=subscription.created_at.timestamp() if subscription.created_at else None,
        )


class BackgroundWork(BaseModel):
    commands: list[BackgroundCommand] = []
    monitors: list[MonitorState] = []
    listeners: list[EventListener] = []


class BackgroundOutput(BaseModel):
    """The last lines a command or monitor wrote."""

    output: str


def _tracked(metadata: dict[str, object], key: str) -> bool:
    value = metadata.get(key)
    return isinstance(value, list) and bool(value)


class ThreadBackgroundWork:
    """What runs in one thread's sandbox on its behalf, for someone allowed to see it.

    The sandbox is read only while the thread's metadata says something runs,
    so opening an idle thread never wakes a stopped sandbox.
    """

    def __init__(self, thread_id: str, metadata: dict[str, object]) -> None:
        self._thread_id = thread_id
        self._metadata = metadata
        self._commands_running = _tracked(metadata, RUNNING_BACKGROUND_TASKS_KEY)
        self._monitors_running = _tracked(metadata, RUNNING_MONITORS_KEY)

    @classmethod
    async def for_reader(cls, thread_id: str, login: str, *, email: str | None) -> Self:
        metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
        # Commands and their output are shell-equivalent, as in the terminal and files.
        _assert_thread_promptable(metadata, login)
        return cls(thread_id, metadata)

    async def listing(self) -> BackgroundWork:
        from openswe.background_tasks import thread_tasks

        work = BackgroundWork(
            listeners=[
                EventListener.of(subscription)
                for subscription in await EventSubscription.for_thread(self._thread_id)
            ]
        )
        sandbox = await self._sandbox()
        if sandbox is None:
            return work
        if self._commands_running:
            work.commands = [
                BackgroundCommand.model_validate(task)
                for task in await thread_tasks(sandbox, self._thread_id, self._metadata)
                if task.get("status") == "running"
            ]
        if self._monitors_running:
            work.monitors = [
                monitor
                for item in await SandboxMonitors(sandbox, self._thread_id).listing()
                if (monitor := MonitorState.model_validate(item)).status == "running"
            ]
        return work

    async def output(self, task_id: str) -> BackgroundOutput:
        """The latest output of ``task_id``, one of this thread's commands or monitors."""
        from openswe.background_tasks import thread_tasks
        from openswe.tools.background_execute import execute

        sandbox = await self._sandbox()
        if sandbox is None:
            raise HTTPException(404, "Nothing is running in this thread.")
        if task_id.startswith(f"{ID_PREFIX}-"):
            try:
                result = await SandboxMonitors(sandbox, self._thread_id).output(
                    task_id, start_line=None, end_line=None
                )
            except MonitorError as exc:
                raise HTTPException(404, "No such monitor.") from exc
            numbered = result.get("output")
            # The script numbers each line as `<number>\t<text>`.
            lines = [
                line.partition("\t")[2]
                for line in (numbered.splitlines() if isinstance(numbered, str) else [])
            ]
            return BackgroundOutput(output="\n".join(lines))
        owned = await thread_tasks(sandbox, self._thread_id, self._metadata)
        if not any(task.get("task_id") == task_id for task in owned):
            raise HTTPException(404, "No such command.")
        output = (await execute(sandbox, "status", task_id=task_id)).get("output")
        lines = output.splitlines() if isinstance(output, str) else []
        return BackgroundOutput(output="\n".join(lines[-OUTPUT_LINES:]))

    async def _sandbox(self) -> SandboxBackendProtocol | None:
        sandbox_id = self._metadata.get("sandbox_id")
        if not (self._commands_running or self._monitors_running) or not isinstance(
            sandbox_id, str
        ):
            return None
        try:
            return await connect_sandbox(sandbox_id, thread_id=self._thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Could not connect to sandbox for background work", extra={"sandbox": sandbox_id}
            )
            raise HTTPException(503, "Could not connect to the workspace.") from exc


async def get_dashboard_thread_background_work(
    thread_id: str, login: str, *, email: str | None = None
) -> BackgroundWork:
    work = await ThreadBackgroundWork.for_reader(thread_id, login, email=email)
    return await work.listing()


async def get_dashboard_thread_background_output(
    thread_id: str, task_id: str, login: str, *, email: str | None = None
) -> BackgroundOutput:
    work = await ThreadBackgroundWork.for_reader(thread_id, login, email=email)
    return await work.output(task_id)
