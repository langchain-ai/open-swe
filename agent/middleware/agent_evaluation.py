"""Collect a coding run's patch without asking the model to reproduce it."""

import logging
from typing import NotRequired

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph.runtime import Runtime

from agent.evaluation import AgentEvaluationArtifact, AgentEvaluationConfig
from agent.middleware.prepare_run import PrepareRunState
from agent.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)
_MAX_PATCH_BYTES = 10 * 1024 * 1024


class AgentEvaluationState(PrepareRunState):
    agent_eval_result: NotRequired[dict[str, object] | None]


class AgentEvaluationMiddleware(OpenSWEMiddleware[AgentEvaluationState]):
    state_schema = AgentEvaluationState

    def __init__(self, backend: SandboxBackendProtocol, options: AgentEvaluationConfig) -> None:
        self._backend = backend
        self._options = options

    async def abefore_agent(
        self, state: AgentEvaluationState, runtime: Runtime
    ) -> dict[str, object]:
        return {"agent_eval_result": None}

    async def aafter_agent(
        self, state: AgentEvaluationState, runtime: Runtime
    ) -> dict[str, object]:
        try:
            artifact = await self._collect()
        except Exception as exc:
            logger.exception("Agent evaluation patch collection failed")
            artifact = AgentEvaluationArtifact(
                status="collection_error", error=f"{type(exc).__name__}: {exc}"
            )
        return {"agent_eval_result": artifact.model_dump()}

    async def _collect(self) -> AgentEvaluationArtifact:
        directory = self._options.checkout_dir
        patch_path = f"{directory}.patch"
        # Both interpolated values are validated shell-safe tokens. Redirecting
        # avoids the execution backend's stdout limit, including for binary diffs.
        commands = (
            f"git -C {directory} add --all",
            f"git -C {directory} diff --cached --binary --no-ext-diff --no-textconv "
            f"{self._options.base_commit} -- > {patch_path}",
        )
        for command in commands:
            response = await self._backend.aexecute(command, timeout=120)
            if response.exit_code != 0:
                raise RuntimeError(f"Patch collection command failed: {response.output}")
        downloads = await self._backend.adownload_files([patch_path])
        if len(downloads) != 1 or downloads[0].error or downloads[0].content is None:
            error = downloads[0].error if downloads else "missing download response"
            raise RuntimeError(f"Patch download failed: {error}")
        content = downloads[0].content
        if len(content) > _MAX_PATCH_BYTES:
            raise ValueError(f"Patch exceeds {_MAX_PATCH_BYTES} bytes")
        return AgentEvaluationArtifact(status="collected", patch=content.decode("utf-8"))
