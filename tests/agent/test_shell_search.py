from collections.abc import Callable, Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from openswe import reviewer
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.prompt import construct_system_prompt
from openswe.prompts import prompt
from openswe.remote_runtime.reviewer import runtime_spec
from openswe.review_scout import graph as scout
from openswe.reviewer import _reviewer_subagent, _reviewer_system_prompt
from openswe.search_policy import SHELL_SEARCH_EXCLUDED_TOOLS
from openswe.server import _general_purpose_subagent
from tests.agent.test_agent_assembly_context import (
    _base_config,
    _capture_create_deep_agent_kwargs,
)
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope


class _ToolRecordingModel(FakeListChatModel):
    offered: list[str] = []

    def bind_tools(
        self,
        tools: Sequence[dict[str, object] | type | Callable[..., object] | BaseTool],
        **kwargs: object,
    ) -> BaseChatModel:
        self.offered = [tool.name for tool in tools if isinstance(tool, BaseTool)]
        return self


@pytest.mark.parametrize(
    "kind", ["main", "reviewer", "main-subagent", "reviewer-subagent", "review-scout"]
)
async def test_shell_agents_offer_execution_without_builtin_grep(
    kind: str, saved_thread_scope: dict[str, object]
) -> None:
    model = _ToolRecordingModel(responses=["Done."])
    system_prompt = (
        construct_system_prompt("/workspace/repo")
        if kind.startswith("main")
        else _reviewer_system_prompt(
            "/workspace/repo", repo_owner="acme", repo_name="repo", pr_number=1
        )
    )
    if kind == "review-scout":
        system_prompt = prompt(
            "review-scout/main",
            pr_number=1,
            repo_full_name="acme/repo",
            pr_title="A change",
            repo_dir="/workspace/repo",
            carried=False,
            plan={
                "chunks": [],
                "other_lines": 0,
                "unplanned": 0,
                "unplanned_hunks": [],
                "more_files_unplanned": 0,
            },
        )
    if kind == "main":
        kwargs = await _capture_create_deep_agent_kwargs()
        assembled = kwargs["middleware"]
    elif kind in {"reviewer", "review-scout"}:
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
        assembled = create.call_args.kwargs["middleware"]
    else:
        assembled = []
    assert isinstance(assembled, list)
    middleware = [item for item in assembled if isinstance(item, ExcludeToolsMiddleware)]
    if kind.endswith("subagent"):
        subagent = (
            _general_purpose_subagent(model, tools=[])
            if kind == "main-subagent"
            else _reviewer_subagent(model)
        )
        middleware = subagent["middleware"]
        system_prompt = subagent.get("system_prompt", system_prompt)
    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        middleware=middleware,
        backend=LocalShellBackend(),
    )

    await agent.ainvoke({"messages": [HumanMessage("Inspect the repository.")]})

    assert "grep" not in model.offered
    assert {"glob", "read_file", "execute"} <= set(model.offered)
    assert prompt("system/shell-search") in system_prompt


def test_remote_reviewer_exports_the_shell_policy_with_subagent_guidance() -> None:
    spec = runtime_spec()
    assert set(spec["excluded_tools"]) == SHELL_SEARCH_EXCLUDED_TOOLS
    assert prompt("system/shell-search") in spec["subagent"]["system_prompt"]
