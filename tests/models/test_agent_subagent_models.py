from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import langgraph_sdk
import pytest
from langgraph.graph.state import RunnableConfig

from openswe.dashboard.settings import Settings
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.server import agent_catalog
from openswe.threads import index

pytestmark = pytest.mark.usefixtures("registry_db")


_MODEL_DEFAULTS = {
    "default_agent_model": "openai:gpt-6.1-sol",
    "default_agent_reasoning_effort": "medium",
    "default_agent_subagent_model": "openai:gpt-6.1-sol",
    "default_agent_subagent_reasoning_effort": "low",
}


@pytest.fixture(autouse=True, params=["public", "private"])
async def saved_thread_scope(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, registry_db
) -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"visibility": request.param, "owner_login": "octocat"}}
            )
        )
    )
    monkeypatch.setattr(langgraph_sdk, "get_client", lambda: client)
    await index.create(
        "thread-123",
        runtime="mda",
        metadata={"visibility": request.param, "owner_login": "octocat"},
    )


@pytest.mark.asyncio
async def test_agent_subagent_uses_global_defaults_despite_profile_override() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-123",
            "github_login": "octocat",
        },
        "metadata": {},
    }
    surface = ToolSurface()

    with (
        patch(
            "openswe.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch("openswe.server.resolve_triggering_user_identity", return_value=None),
        patch(
            "openswe.server.resolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "openswe.server.cached_settings",
            new_callable=AsyncMock,
            return_value=Settings(_MODEL_DEFAULTS),
        ),
        patch(
            "openswe.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-opus-5-5",
                "reasoning_effort": "high",
            },
        ),
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
    ):
        await agent_catalog(config, surface)

    assert surface.models is not None
    assert surface.models.subagent == surface.models.main
    assert surface.models.main.model_id == "openai:gpt-6.1-sol"
    assert surface.models.main.kwargs["reasoning"]["effort"] == "medium"


@pytest.mark.asyncio
async def test_agent_ignores_disabled_fable_profile() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-123",
            "github_login": "octocat",
        },
        "metadata": {},
    }
    surface = ToolSurface()

    with (
        patch(
            "openswe.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch("openswe.server.resolve_triggering_user_identity", return_value=None),
        patch(
            "openswe.server.resolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "openswe.server.cached_settings",
            new_callable=AsyncMock,
            return_value=Settings({**_MODEL_DEFAULTS, "fable_enabled": False}),
        ),
        # Profile selected Fable back when it was allowed; it's now disabled.
        patch(
            "openswe.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-fable-5-2",
                "reasoning_effort": "high",
            },
        ),
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
    ):
        await agent_catalog(config, surface)

    assert surface.models is not None
    assert surface.models.main.model_id == "openai:gpt-6.1-sol"
    assert surface.models.main.kwargs["reasoning"]["effort"] == "medium"
    assert surface.models.subagent == surface.models.main
