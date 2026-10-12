"""Give a desktop thread's placeholder worktree branch a name that says what it is for.

The desktop app checks a new worktree out on ``open-swe/local-<hex>`` because
it has nothing better to call it yet. The first run renames it, through the
thread's bridge, since the checkout is on the user's machine. Only a branch
still carrying that exact placeholder shape is ever renamed, so a branch the
user named, or one already renamed, is left alone.
"""

import asyncio
import contextvars
import logging
import re
import shlex
from collections.abc import Sequence

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from openswe.input_messages import dynamic_context_hash, input_message_text
from openswe.prompts import prompt

logger = logging.getLogger(__name__)

BRANCH_PREFIX = "open-swe"
TEMPORARY_BRANCH_PREFIX = f"{BRANCH_PREFIX}/local"
MAX_BRANCH_INPUT_CHARS = 4_000
MAX_BRANCH_SLUG_CHARS = 48
BRANCH_GENERATION_TIMEOUT_SECONDS = 10
_TEMPORARY_BRANCH = re.compile(rf"^{re.escape(TEMPORARY_BRANCH_PREFIX)}-[0-9a-f]{{8}}$")
_background_tasks: set[asyncio.Task[None]] = set()
_inflight_threads: set[str] = set()

_BRANCH_SYSTEM_PROMPT = prompt("worktree-branch")


class _BranchName(BaseModel):
    branch: str = Field(description="Hyphenated lowercase branch name, 2-5 words")


def is_temporary_branch(branch: str) -> bool:
    return bool(_TEMPORARY_BRANCH.match(branch.strip().lower()))


def build_branch_name(raw: str) -> str | None:
    slug = (
        re.sub(r"[^a-z0-9]+", "-", raw.strip().lower())
        .strip("-")[:MAX_BRANCH_SLUG_CHARS]
        .rstrip("-")
    )
    return f"{BRANCH_PREFIX}/{slug}" if slug else None


def _request_text(messages: Sequence[BaseMessage]) -> str | None:
    for message in messages:
        if message.type != "human" or dynamic_context_hash(message.content) is not None:
            continue
        text = (input_message_text(message.content) or message.text).strip()
        if text:
            return text[:MAX_BRANCH_INPUT_CHARS]
    return None


async def _git(backend: SandboxBackendProtocol, *args: str) -> str | None:
    result = await backend.aexecute(shlex.join(["git", *args]) + " 2>/dev/null")
    return result.output.strip() if result.exit_code == 0 else None


async def rename_temporary_worktree_branch(
    *, backend: SandboxBackendProtocol, request: str, model: BaseChatModel
) -> str | None:
    """Rename a placeholder worktree branch to one that describes the request."""
    current = await _git(backend, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not current or not is_temporary_branch(current):
        return None

    structured = model.with_structured_output(_BranchName)
    async with asyncio.timeout(BRANCH_GENERATION_TIMEOUT_SECONDS):
        result = await structured.ainvoke(
            [
                SystemMessage(content=_BRANCH_SYSTEM_PROMPT),
                HumanMessage(content=request),
            ],
            # Empty callbacks, so this call cannot inherit the run's handlers and
            # stream its tokens into the thread the user is watching.
            config={"callbacks": [], "run_name": "worktree-branch-name"},
        )
    if not isinstance(result, _BranchName):
        return None
    target = build_branch_name(result.branch)
    if not target or target == current:
        return None
    for candidate in (target, *(f"{target}-{suffix}" for suffix in range(2, 10))):
        if await _git(backend, "branch", "-m", "--", current, candidate) is not None:
            return candidate
    return None


def schedule_worktree_branch_rename(
    *,
    thread_id: str,
    backend: SandboxBackendProtocol,
    messages: Sequence[BaseMessage],
    model: BaseChatModel,
) -> None:
    """Name the thread's worktree branch without blocking the run."""
    request = _request_text(messages)
    if not request or thread_id in _inflight_threads:
        return
    _inflight_threads.add(thread_id)

    async def run() -> None:
        try:
            await rename_temporary_worktree_branch(backend=backend, request=request, model=model)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Worktree branch rename failed",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )
        finally:
            _inflight_threads.discard(thread_id)

    # A fresh context, not the caller's: an inherited context carries LangGraph's
    # stream writer, and this call's structured-output chunks would then be
    # emitted into the run's message stream.
    task = asyncio.get_running_loop().create_task(run(), context=contextvars.Context())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
