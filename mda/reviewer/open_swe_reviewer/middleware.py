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
from importlib.resources import files
from typing import Annotated, Final, NotRequired

from deepagents.backends.protocol import BackendProtocol
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

from open_swe_reviewer.backend import BackendCallError, OpenSweBackend
from open_swe_reviewer.models import ModelSpec, build_model

logger = logging.getLogger(__name__)

# Preparation waits on the review scout's walkthrough for up to ten minutes.
_PREPARE_TIMEOUT_SECONDS: Final = 1800.0
_HOOK_TIMEOUT_SECONDS: Final = 120.0
_CHECKOUT_TIMEOUT_SECONDS: Final = 600
_CHECKOUT_SCRIPT: Final = files("open_swe_reviewer").joinpath("checkout.sh").read_bytes()
_CHECKOUT_SCRIPT_PATH: Final = "/tmp/open-swe-checkout.sh"


async def _upload(backend: BackendProtocol, path: str, content: bytes) -> None:
    uploads = await backend.aupload_files([(path, content)])
    if uploads and uploads[0].error:
        raise RuntimeError(f"Writing {path} failed: {uploads[0].error}")


class CheckoutSpec(BaseModel):
    repository: str
    pr_number: int | None
    base_sha: str
    head_sha: str
    repo_dir: str
    diff_base_ref: str
    diff_path: str
    diff_text: str

    def _environment(self) -> dict[str, str]:
        environment = {
            "REPO_URL": f"https://github.com/{self.repository}.git",
            "REPO_DIR": self.repo_dir,
            "HEAD_SHA": self.head_sha,
            "BASE_SHA": self.base_sha,
        }
        if self.pr_number is not None:
            environment["PULL_REF"] = f"refs/pull/{self.pr_number}/head"
        return environment

    async def check_out(self, runtime: Runtime) -> None:
        """Check the pull request head out in the sandbox and write the review diff beside it."""
        if not isinstance(runtime, ManagedRuntime) or runtime.backend is None:
            raise RuntimeError("The reviewer needs this deployment's sandbox")
        await _upload(runtime.backend, _CHECKOUT_SCRIPT_PATH, _CHECKOUT_SCRIPT)
        assignments = " ".join(
            f"{name}={shlex.quote(value)}" for name, value in self._environment().items()
        )
        result = await runtime.backend.aexecute(
            f"env {assignments} bash {_CHECKOUT_SCRIPT_PATH}",
            timeout=_CHECKOUT_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"Checking out {self.repository} failed with exit code {result.exit_code}: "
                f"{result.output}"
            )
        # The diff lives inside the checkout, so it is written only once the clone exists.
        await _upload(runtime.backend, self.diff_path, self.diff_text.encode())


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


class _RunConfigurable(BaseModel):
    thread_id: str


class _Drained(BaseModel):
    messages: list[JsonValue]


def prepared_run(state: RunState) -> PreparedRun | None:
    raw = state.get("open_swe_run")
    return None if raw is None else PreparedRun.model_validate(raw)


def _with_prompt(request: ModelRequest, prompt: str) -> SystemMessage:
    existing = request.system_message.text if request.system_message is not None else ""
    return SystemMessage(content=f"{prompt}\n\n{existing}" if existing else prompt)


class BackendRunMiddleware(AgentMiddleware[RunState]):
    """Prepare each run on the backend and apply that preparation to the reviewer."""

    state_schema = RunState

    def __init__(self, backend: OpenSweBackend, invocation_id: str | None) -> None:
        self._backend = backend
        self._invocation_id = invocation_id

    def _invocation(self) -> str:
        if self._invocation_id is not None:
            return self._invocation_id
        return _RunConfigurable.model_validate(get_config().get("configurable", {})).thread_id

    async def abefore_agent(
        self,
        state: RunState,
        runtime: Runtime,
    ) -> dict[str, JsonValue] | None:
        invocation = self._invocation()
        if state.get("open_swe_prepared_for") == invocation and prepared_run(state) is not None:
            return None
        prepared = PreparedRun.model_validate(
            await self._backend.call("prepare", timeout_seconds=_PREPARE_TIMEOUT_SECONDS)
        )
        if prepared.checkout is not None:
            await prepared.checkout.check_out(runtime)
        return {"open_swe_prepared_for": invocation, "open_swe_run": prepared.model_dump()}

    async def abefore_model(
        self,
        state: RunState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, JsonValue] | None:
        drained = _Drained.model_validate(
            await self._backend.call("drain", timeout_seconds=_HOOK_TIMEOUT_SECONDS)
        )
        return {"messages": drained.messages} if drained.messages else None

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
            await self._backend.call("settle", timeout_seconds=_HOOK_TIMEOUT_SECONDS)
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
