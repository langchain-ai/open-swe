from __future__ import annotations

import logging
from typing import Any, NotRequired, cast

from deepagents.middleware.skills import SkillsMiddleware, SkillsState, SkillsStateUpdate
from langgraph.graph.state import RunnableConfig
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class TrustedSkillsState(SkillsState):
    trusted_skills_ref: NotRequired[str]


class TrustedSkillsMiddleware(SkillsMiddleware):
    state_schema = TrustedSkillsState

    def __init__(self, *, trusted_ref: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._trusted_ref = trusted_ref

    @property
    def name(self) -> str:
        return "SkillsMiddleware"

    async def abefore_agent(
        self,
        state: SkillsState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> SkillsStateUpdate | None:
        trusted_state = cast(TrustedSkillsState, state)
        if (
            trusted_state.get("trusted_skills_ref") == self._trusted_ref
            and "skills_metadata" in trusted_state
        ):
            return None

        reload_state = dict(trusted_state)
        reload_state.pop("skills_metadata", None)
        reload_state.pop("skills_load_errors", None)
        update = await super().abefore_agent(cast(SkillsState, reload_state), runtime, config) or {}
        metadata = update.get("skills_metadata", [])
        errors = update.get("skills_load_errors", [])
        logger.info(
            "Loaded trusted repository skills valid=%d failures=%d",
            len(metadata),
            len(errors),
        )
        return cast(
            SkillsStateUpdate,
            {
                **update,
                "skills_load_errors": errors,
                "trusted_skills_ref": self._trusted_ref,
            },
        )
