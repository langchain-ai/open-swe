"""The run hooks that tie this runtime's model loop to the Open SWE backend.

The backend prepares each run (diff, rendered review prompt, model choice) and
owns everything with credentials. These hooks fetch that preparation once per
run, check the pull request out in this deployment's sandbox, apply the
preparation to every model call, and give the backend its per-call and
end-of-run turns.
"""

import logging
import shlex
from collections.abc import Awaitable, Callable
from typing import Annotated, Final, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
)
from langchain_core.messages import SystemMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from managed_deepagents import ManagedRuntime
from pydantic import BaseModel, JsonValue

from open_swe_reviewer.backend import (
    DRAIN_HOOK,
    PREPARE_HOOK,
    SETTLE_HOOK,
    BackendCallError,
    call_backend,
    current_thread_id,
)
from open_swe_reviewer.models import ModelSpec, build_model

logger = logging.getLogger(__name__)

# Preparation waits on the review scout's walkthrough for up to ten minutes.
_PREPARE_TIMEOUT_SECONDS: Final = 1800.0
_HOOK_TIMEOUT_SECONDS: Final = 120.0
_CHECKOUT_TIMEOUT_SECONDS: Final = 240


class CheckoutSpec(BaseModel):
    repository: str
    pr_number: int | None
    base_sha: str
    head_sha: str
    repo_dir: str
    diff_base_ref: str
    diff_path: str
    diff_text: str

    def command(self) -> str:
        """Clone or fetch the public repository and check out the pull request head."""
        repo_dir = shlex.quote(self.repo_dir)
        head = shlex.quote(self.head_sha)
        url = shlex.quote(f"https://github.com/{self.repository}.git")
        lines = [
            "set -e",
            f"if [ -d {repo_dir}/.git ]; then",
            f"  cd {repo_dir} && {{ git fetch --all --quiet || true; }}",
            "else",
            f"  git clone --quiet {url} {repo_dir} && cd {repo_dir}",
            "fi",
        ]
        if self.base_sha:
            lines.append(
                f"git fetch origin {shlex.quote(self.base_sha)} --quiet 2>/dev/null || true"
            )
        lines.append(f"git fetch origin {head} --quiet 2>/dev/null || true")
        if self.pr_number is not None:
            # Fork pull requests are only reachable through their pull ref.
            pull_ref = shlex.quote(f"refs/pull/{self.pr_number}/head")
            lines.append(f"git fetch origin {pull_ref} --quiet 2>/dev/null || true")
        lines.append(f"git checkout --force {head} --quiet")
        lines.append(f'[ "$(git rev-parse HEAD)" = {head} ]')
        return "\n".join(lines)

    async def check_out(self, runtime: Runtime) -> None:
        if not isinstance(runtime, ManagedRuntime) or runtime.backend is None:
            raise RuntimeError("The reviewer needs this deployment's sandbox")
        result = await runtime.backend.aexecute(self.command(), timeout=_CHECKOUT_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            raise RuntimeError(f"Checking out {self.repository} failed: {result.output}")
        uploads = await runtime.backend.aupload_files([(self.diff_path, self.diff_text.encode())])
        if uploads and uploads[0].error:
            raise RuntimeError(f"Writing the review diff failed: {uploads[0].error}")


class PreparedRun(BaseModel):
    system_prompt: str
    work_dir: str
    checkout: CheckoutSpec | None
    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool


class RunState(AgentState):
    open_swe_prepared_for: NotRequired[Annotated[str, OmitFromOutput]]
    open_swe_run: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput]]


def _invocation_id() -> str:
    invocation = get_config().get("configurable", {}).get("invocation_id")
    return invocation if isinstance(invocation, str) and invocation else current_thread_id()


def prepared_run(state: RunState) -> PreparedRun | None:
    raw = state.get("open_swe_run")
    return None if raw is None else PreparedRun.model_validate(raw)


def _with_prompt(request: ModelRequest, prompt: str) -> SystemMessage:
    existing = request.system_message.text if request.system_message is not None else ""
    return SystemMessage(content=f"{prompt}\n\n{existing}" if existing else prompt)


class BackendRunMiddleware(AgentMiddleware[RunState]):
    """Prepare each run on the backend and apply that preparation to the reviewer."""

    state_schema = RunState

    async def abefore_agent(
        self,
        state: RunState,
        runtime: Runtime,
    ) -> dict[str, JsonValue] | None:
        invocation = _invocation_id()
        if state.get("open_swe_prepared_for") == invocation and prepared_run(state) is not None:
            return None
        prepared = PreparedRun.model_validate(
            await call_backend(PREPARE_HOOK, {}, timeout_seconds=_PREPARE_TIMEOUT_SECONDS)
        )
        if prepared.checkout is not None:
            await prepared.checkout.check_out(runtime)
        return {"open_swe_prepared_for": invocation, "open_swe_run": prepared.model_dump()}

    async def abefore_model(
        self,
        state: RunState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, JsonValue] | None:
        drained = await call_backend(DRAIN_HOOK, {}, timeout_seconds=_HOOK_TIMEOUT_SECONDS)
        messages = drained.get("messages")
        return {"messages": messages} if isinstance(messages, list) and messages else None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prepared = prepared_run(request.state)
        if prepared is None:
            return await handler(request)
        return await handler(
            request.override(
                model=build_model(prepared.model, use_gateway=prepared.use_gateway),
                system_message=_with_prompt(request, prepared.system_prompt),
            )
        )

    async def aafter_agent(
        self,
        state: RunState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, JsonValue] | None:
        try:
            await call_backend(SETTLE_HOOK, {}, timeout_seconds=_HOOK_TIMEOUT_SECONDS)
        except BackendCallError:
            # The backend's completion webhook settles a check this run left open.
            logger.warning("Settling the review check run failed", exc_info=True)
        return None


class SubagentRunMiddleware(AgentMiddleware[RunState]):
    """Run the reviewer subagent on the subagent model the backend picked."""

    state_schema = RunState

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prepared = prepared_run(request.state)
        if prepared is None:
            return await handler(request)
        return await handler(
            request.override(
                model=build_model(prepared.subagent_model, use_gateway=prepared.use_gateway)
            )
        )
