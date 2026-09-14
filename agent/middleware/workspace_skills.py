"""Exclude personal skill context retained by public thread checkpoints."""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from deepagents.middleware.skills import SkillsMiddleware, SkillsState, SkillsStateUpdate
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime


class WorkspaceSkillsMiddleware(SkillsMiddleware):
    @property
    def name(self) -> str:
        # Replace the built-in middleware in its existing stack position.
        return "SkillsMiddleware"

    def _scoped_state(self, state: dict[str, Any]) -> SkillsState:
        scoped = dict(state)
        if "skills_metadata" in scoped:
            scoped["skills_metadata"] = [
                skill
                for skill in scoped["skills_metadata"]
                if isinstance(skill, dict)
                and isinstance(skill.get("path"), str)
                and skill["path"].startswith(tuple(self.sources))
            ]
        scoped["skills_load_errors"] = []
        return cast(SkillsState, scoped)

    async def abefore_agent(
        self, state: SkillsState, runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        scoped = self._scoped_state(cast(dict[str, Any], state))
        loaded = await super().abefore_agent(scoped, runtime, config)
        return loaded or {
            "skills_metadata": scoped.get("skills_metadata", []),
            "skills_load_errors": [],
        }

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        request = request.override(state=self._scoped_state(cast(dict[str, Any], request.state)))
        return await super().awrap_model_call(request, handler)
