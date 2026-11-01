"""Reflect on an idle thread: fork it, and let the fork only remember what it learned.

Once a thread of someone with the Memory experiment has been idle for ``IDLE`` after
substantial work (it ran shell commands, edited more than ``_EDITED_FILES`` files or
answered more than ``_USER_TURNS`` messages), it is copied, every checkpoint with it,
and the copy runs one more turn told to save its lessons with ``remember``.

The point is the provider's prompt cache: the fork's model calls must start exactly
as its source's did, so they read the cache the source's last call wrote. The fork's
run is planned from the source run's own configuration, every tool stays offered and
those but ``REFLECTION_TOOLS`` are refused when called (``Catalog.runnable_tools``),
the system prompt is the source's, fixed in the copied state, and the route is the one
the source's calls took. The run starts only when its catalog puts the same tools,
models and prompts ahead of the conversation as the source run's did
(``Catalog.request_prefix``); otherwise it would pay for the whole conversation again.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Final, Self

from pydantic import BaseModel, JsonValue, ValidationError

from openswe import event_claims
from openswe.agent_api.connections import acting_user
from openswe.agent_api.tokens import RemoteRun
from openswe.dispatch import channel_of, create_durable_run
from openswe.input_messages import build_run_input, message_sender_id
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.memories.store import MEMORIES
from openswe.prompts import prompt
from openswe.run_config import MemoryReflectionRun, RunConfig
from openswe.threads.index import IndexedThread
from openswe.threads.store import THREADS
from openswe.users import User

logger = logging.getLogger(__name__)

JOB: Final = "memory_reflection"
REFLECTION_TOOLS: Final = ("remember", "forget")
IDLE: Final = timedelta(minutes=4)
# The source thread's metadata: how many of its messages a fork has reflected on.
REFLECTED_THROUGH_KEY: Final = "memory_reflected_through"
# A fork's metadata: the thread it was copied from.
FORK_OF_KEY: Final = "memory_reflection_of"
_SENDER_ID: Final = "system:memory-reflection"
_SHELL_TOOLS: Final = frozenset({"execute", "background_execute"})
_EDIT_TOOLS: Final = frozenset({"edit_file", "write_file"})
_EDITED_FILES: Final = 5
_USER_TURNS: Final = 3
_CLAIM_TTL: Final = timedelta(days=1)
# Run config keys one invocation stamps, which the fork's run gets afresh.
_INVOCATION_KEYS: Final = frozenset(
    {"thread_id", "run_id", "invocation_id", "prepare_run_id", "invocation_started_at"}
)


class PromptCacheMissError(RuntimeError):
    """A reflection fork's run would not start its model calls as its source's did."""

    def __init__(self, source_thread_id: str) -> None:
        super().__init__(
            f"A reflection fork of {source_thread_id} would miss its prompt cache: its "
            "tools, models or prompts differ from the source run's"
        )


class _ToolCall(BaseModel):
    name: str = ""
    args: dict[str, JsonValue] = {}


class _Message(BaseModel):
    type: str = ""
    content: JsonValue = None
    tool_calls: list[_ToolCall] = []


class _Values(BaseModel):
    messages: list[_Message] = []


class _State(BaseModel):
    values: _Values = _Values()


class ThreadActivity:
    """What a thread did in its messages since a reflection last looked at it."""

    def __init__(self, messages: list[_Message]) -> None:
        self.messages = messages

    @property
    def calls(self) -> list[_ToolCall]:
        return [call for message in self.messages for call in message.tool_calls]

    @property
    def ran_shell(self) -> bool:
        return any(call.name in _SHELL_TOOLS for call in self.calls)

    @property
    def edited_files(self) -> int:
        return len(
            {
                str(call.args.get("file_path") or call.args.get("path"))
                for call in self.calls
                if call.name in _EDIT_TOOLS
            }
        )

    @property
    def user_turns(self) -> int:
        return sum(
            message.type == "human" and message_sender_id(message.content, kind="human") is not None
            for message in self.messages
        )

    @property
    def substantial(self) -> bool:
        return self.ran_shell or self.edited_files > _EDITED_FILES or self.user_turns > _USER_TURNS


class IdleReflection(BaseModel):
    """A thread's last run, which a fork of the thread reflects on once it has been idle."""

    thread_id: str
    # The run as dispatch stamped it, which the fork's run is planned from.
    configurable: dict[str, JsonValue]
    acts_for_person: bool
    request_prefix: str

    @classmethod
    async def schedule(cls, assistant_id: str, run: RemoteRun) -> None:
        """Reflect on ``run``'s thread once it stays idle for ``IDLE``; a later run's end
        moves that on. Never raises: a run's end must not fail over it."""
        try:
            reflection = await cls._of(assistant_id, run)
            if reflection is None:
                return
            await queue.schedule(
                JOB,
                reflection.model_dump(mode="json"),
                due_at=datetime.now(UTC) + IDLE,
                key=f"{JOB}:{run.thread_id}",
            )
        except Exception:
            logger.exception(
                "Could not schedule a memory reflection", extra={"thread_id": run.thread_id}
            )

    @classmethod
    async def _of(cls, assistant_id: str, run: RemoteRun) -> Self | None:
        cfg = RunConfig.parse(run.configurable)
        turn = run.settings.agent
        if (
            assistant_id != "agent"
            or cfg.memory_reflection is not None
            or turn is None
            or turn.task_worker
            or not turn.profile_login
            or run.settings.request_prefix is None
            # The backend cannot copy a thread an MDA channel owns.
            or channel_of(run.metadata) is not None
        ):
            return None
        if not (await User.preferences_for_login(turn.profile_login)).experimental_memory:
            return None
        return cls(
            thread_id=run.thread_id,
            configurable=run.configurable,
            acts_for_person=acting_user(run.metadata) is not None,
            request_prefix=run.settings.request_prefix,
        )

    @classmethod
    async def fire(cls, job: Job) -> None:
        try:
            reflection = cls.model_validate(job.payload)
        except ValidationError:
            logger.warning("Dropped a malformed memory reflection", extra={"job_id": str(job.id)})
            return
        await reflection.reflect()

    async def reflect(self) -> None:
        """Fork the thread and start the fork's run, if it is idle and did something worth it."""
        thread = await THREADS.get(self.thread_id)
        if thread is None or thread.status != "idle" or channel_of(thread.metadata) is not None:
            return
        cfg = RunConfig.parse(self.configurable)
        login = cfg.github_login or ""
        if not (await User.preferences_for_login(login)).experimental_memory:
            return
        state = await THREADS.state(self.thread_id)
        messages = _State.model_validate(state or {}).values.messages
        through = thread.metadata.get(REFLECTED_THROUGH_KEY)
        since = through if isinstance(through, int) and 0 <= through <= len(messages) else 0
        if not ThreadActivity(messages[since:]).substantial:
            return
        # Once for these messages: a job claimed again after a crash may have forked already.
        if not await event_claims.claim(JOB, f"{self.thread_id}:{len(messages)}", ttl=_CLAIM_TTL):
            return
        title = thread.metadata.get("title")
        fork = await THREADS.copy(
            thread,
            {
                "unlisted": True,
                FORK_OF_KEY: self.thread_id,
                "title": f"Memory reflection: {title}" if isinstance(title, str) else None,
            },
        )
        try:
            await self._start(fork)
        except Exception:
            logger.exception(
                "Could not start a memory reflection fork",
                extra={"thread_id": self.thread_id, "fork_thread_id": fork.thread_id},
            )
            await THREADS.discard_copy(fork)
            return
        await THREADS.update(self.thread_id, {REFLECTED_THROUGH_KEY: len(messages)})
        logger.info(
            "Forked an idle thread to reflect on its memories",
            extra={"thread_id": self.thread_id, "fork_thread_id": fork.thread_id},
        )

    async def _start(self, fork: IndexedThread) -> None:
        reflection = MemoryReflectionRun(
            source_thread_id=self.thread_id, request_prefix=self.request_prefix
        )
        configurable = {
            key: value for key, value in self.configurable.items() if key not in _INVOCATION_KEYS
        } | {
            "thread_id": fork.thread_id,
            "memory_reflection": reflection.model_dump(mode="json"),
        }
        cfg = RunConfig.parse(configurable)
        await create_durable_run(
            fork.thread_id,
            "agent",
            input=build_run_input(
                prompt(
                    "memory/reflection", memories=await MEMORIES.saved_in_thread(self.thread_id)
                ),
                {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
                systems=[
                    {"id": _SENDER_ID, "display_name": "Memory reflection", "platform": "open-swe"}
                ],
            ),
            source=cfg.source or "dashboard",
            thread_title=None,
            config={"configurable": with_invocation_id(configurable, new_invocation_id())},
            metadata={"kind": JOB, FORK_OF_KEY: self.thread_id},
            multitask_strategy="reject",
            # As the source run acted, since that decides the tools the run is offered.
            as_sender=self.acts_for_person,
        )
