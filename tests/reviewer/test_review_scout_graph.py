from types import SimpleNamespace
from unittest.mock import AsyncMock

from langsmith.sandbox import SandboxOperationError

from agent.review_scout import graph


async def test_store_walkthrough_ignores_sandbox_finalize_error(monkeypatch) -> None:
    cfg = SimpleNamespace(
        repo=SimpleNamespace(owner="acme", name="project", full_name="acme/project"),
        pr_number=7,
        head_sha="a" * 40,
    )
    monkeypatch.setattr(graph.RunConfig, "from_config", lambda config: cfg)
    monkeypatch.setattr(graph, "get_cached_sandbox_backend", lambda thread_id: object())
    monkeypatch.setattr(graph, "scout_repo_dir", AsyncMock(return_value="/repo"))
    monkeypatch.setattr(
        graph,
        "finalize",
        AsyncMock(side_effect=SandboxOperationError("argument list too long")),
    )

    middleware = graph.StoreWalkthroughMiddleware(thread_id="thread", config={})

    await middleware.aafter_agent({"scout_merge_base": "b" * 40}, None)
