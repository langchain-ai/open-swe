"""The skills a run may load, listed in each model call's system prompt.

A thread anyone in the workspace can open leaves out personal skills its
checkpoints kept.
"""

from collections.abc import Mapping
from typing import Any, cast

from deepagents.middleware.skills import (
    SkillMetadata,
    SkillsMiddleware,
    SkillsState,
    SkillsStateUpdate,
)
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from openswe.middleware.model_call_shape import CallShape


class RunSkillsMiddleware(SkillsMiddleware):
    """deepagents' skills, whose section each model call's system prompt ends with."""

    async def ashape_model_call(
        self, call: CallShape, state: Mapping[str, object], runtime: Runtime
    ) -> CallShape:
        if self.system_prompt_template is None:
            return call
        listed = state.get("skills_metadata")
        errors = state.get("skills_load_errors")
        return call.appending(
            self.system_prompt_template.format(
                skills_locations=self._format_skills_locations(),
                skills_load_warnings=self._format_skills_load_warnings(
                    [error for error in errors if isinstance(error, str)]
                    if isinstance(errors, list)
                    else []
                ),
                skills_list=self._format_skills_list(
                    [cast(SkillMetadata, skill) for skill in listed if isinstance(skill, dict)]
                    if isinstance(listed, list)
                    else []
                ),
            )
        )


class WorkspaceSkillsMiddleware(RunSkillsMiddleware):
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
        if loaded:
            return loaded
        return SkillsStateUpdate(
            skills_metadata=scoped.get("skills_metadata") or [],
            skills_load_errors=[],
        )

    async def ashape_model_call(
        self, call: CallShape, state: Mapping[str, object], runtime: Runtime
    ) -> CallShape:
        scoped = self._scoped_state(dict(state))
        return await super().ashape_model_call(call, cast(Mapping[str, object], scoped), runtime)
