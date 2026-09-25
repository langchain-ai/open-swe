"""Experimental features a person opts into, frozen onto the threads they start.

A person's choice lives in their preferences. A thread copies its creator's
choices on its first run and keeps them, so a feature's effective value always
comes from the thread: flipping a preference changes only threads started
afterwards, and other participants never switch it for the creator's threads.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_settings import ThreadSettings, load_thread_settings

ExperimentalFeature = Literal["pr_comment_triggers"]


class ExperimentalFeatures(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # On PRs the agent opened, Open SWE users' comments and reviews wake it without a tag.
    pr_comment_triggers: bool = False

    def enabled(self, feature: ExperimentalFeature) -> bool:
        return getattr(self, feature)

    def thread_value(self) -> dict[str, bool]:
        return self.model_dump()

    @classmethod
    def from_thread_settings(cls, settings: ThreadSettings) -> Self:
        return cls.model_validate(settings.get("experimental", {}))

    @classmethod
    async def for_thread(cls, thread_id: str) -> Self:
        return cls.from_thread_settings(await load_thread_settings(langgraph_client(), thread_id))


class ExperimentalFeaturesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_comment_triggers: bool | None = None
