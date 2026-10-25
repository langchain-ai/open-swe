"""The pull request reviewer, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig

from openswe.agent_api.graph import RemoteGraph
from openswe.review.diff import compute_diff_line_set
from openswe.reviewer import plan_reviewer
from openswe.sandboxes.tool_runtime import ToolSurface

ASSISTANT_ID: Final = "reviewer"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    await plan_reviewer(config, surface)


def _restore(state: dict[str, object]) -> None:
    # Sets do not cross to MDA and back; the diff they index does.
    diff_text = state.get("diff_text")
    if isinstance(diff_text, str) and diff_text and "diff_line_set" not in state:
        state["diff_line_set"] = compute_diff_line_set(diff_text)


REVIEWER: Final = RemoteGraph(ASSISTANT_ID, _plan, restore_state=_restore)
