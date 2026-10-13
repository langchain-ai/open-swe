from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from openswe import reviewer
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.review_scout import graph as scout
from openswe.reviewer import _reviewer_subagent
from openswe.search_policy import SHELL_SEARCH_EXCLUDED_TOOLS
from openswe.server import _general_purpose_subagent
from tests.agent.test_agent_assembly_context import (
    _base_config,
    _capture_create_deep_agent_kwargs,
)
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope


@pytest.mark.parametrize(
    "kind", ["main", "reviewer", "main-subagent", "reviewer-subagent", "review-scout"]
)
async def test_shell_agent_factories_apply_shared_search_policy(
    kind: str, saved_thread_scope: dict[str, object]
) -> None:
    model = FakeListChatModel(responses=["Done."])
    if kind == "main":
        assembled = await _capture_create_deep_agent_kwargs()
    elif kind.endswith("subagent"):
        assembled = (
            _general_purpose_subagent(model, tools=[])
            if kind == "main-subagent"
            else _reviewer_subagent(model)
        )
    else:
        module = reviewer if kind == "reviewer" else scout
        factory = reviewer.get_reviewer_agent if kind == "reviewer" else scout.get_review_scout
        with (
            patch.object(module, "create_deep_agent", return_value=MagicMock()) as create,
            patch.object(module, "_make_model_or_defer", return_value=model),
            patch.object(
                module, "cached_workspace_settings", AsyncMock(return_value=WorkspaceSettings({}))
            ),
        ):
            await factory(_base_config())
        assembled = create.call_args.kwargs
    middleware = assembled["middleware"]
    assert isinstance(middleware, list)
    policies = [item._excluded for item in middleware if isinstance(item, ExcludeToolsMiddleware)]
    assert policies == [SHELL_SEARCH_EXCLUDED_TOOLS]
