"""Exercise patch collection with real Git and the middleware graph lifecycle."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from deepagents.backends import LocalShellBackend
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from agent import server
from agent.evaluation import AgentEvaluationArtifact, AgentEvaluationConfig
from agent.middleware.agent_evaluation import AgentEvaluationMiddleware
from agent.run_config import RunConfig
from evals.agent.judge import _run

_CHECKOUT = "eval-0123456789abcdef"


async def _git(root: Path, *arguments: str) -> str:
    result = await _run(["git", *arguments], root, 30)
    assert result["returncode"] == 0, result["output"]
    return result["output"].strip()


@pytest.mark.parametrize("committed", [False, True])
async def test_collects_committed_unstaged_and_untracked_changes(
    tmp_path: Path, committed: bool
) -> None:
    checkout = tmp_path / _CHECKOUT
    checkout.mkdir()
    await _git(checkout, "init", "-q")
    await _git(checkout, "config", "core.autocrlf", "false")
    (checkout / "code.txt").write_text("before\n", encoding="utf-8")
    await _git(checkout, "add", ".")
    await _git(
        checkout,
        "-c",
        "user.name=Eval",
        "-c",
        "user.email=eval@example.com",
        "commit",
        "-qm",
        "base",
    )
    base = await _git(checkout, "rev-parse", "HEAD")
    (checkout / "code.txt").write_text("after\n", encoding="utf-8")
    if committed:
        await _git(checkout, "add", ".")
        await _git(
            checkout,
            "-c",
            "user.name=Eval",
            "-c",
            "user.email=eval@example.com",
            "commit",
            "-qm",
            "fix",
        )
    # Larger than stdout limits: the patch must travel through the file API.
    (checkout / "new.txt").write_text("new evidence\n" * 12000, encoding="utf-8")
    backend = LocalShellBackend(root_dir=tmp_path, env=dict(os.environ), max_output_bytes=32)
    middleware = AgentEvaluationMiddleware(
        backend, AgentEvaluationConfig(base_commit=base, checkout_dir=_CHECKOUT)
    )
    graph = create_agent(
        FakeMessagesListChatModel(responses=[AIMessage(content="Done")]),
        middleware=[middleware],
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "Finish"}]})
    artifact = AgentEvaluationArtifact.model_validate(result["agent_eval_result"])
    assert artifact.status == "collected"
    assert "-before" in artifact.patch
    assert "+after" in artifact.patch
    assert "diff --git a/new.txt b/new.txt" in artifact.patch
    assert len(artifact.patch) > 100_000


async def test_collection_error_is_returned_in_graph_state(tmp_path: Path) -> None:
    backend = LocalShellBackend(root_dir=tmp_path, env=dict(os.environ))
    graph = create_agent(
        FakeMessagesListChatModel(responses=[AIMessage(content="Done")]),
        middleware=[
            AgentEvaluationMiddleware(
                backend, AgentEvaluationConfig(base_commit="a" * 40, checkout_dir=_CHECKOUT)
            )
        ],
    )
    result = await graph.ainvoke({"messages": []})
    artifact = AgentEvaluationArtifact.model_validate(result["agent_eval_result"])
    assert artifact.status == "collection_error"
    assert artifact.patch == ""
    assert artifact.error


async def test_eval_preparation_does_not_require_github_or_generate_a_title(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = LocalShellBackend(root_dir=tmp_path, env=dict(os.environ))
    proxy = Mock()
    proxy.ready = AsyncMock(return_value=backend)
    monkeypatch.setattr(server, "get_or_create_sandbox_backend_proxy", lambda thread_id: proxy)
    title = Mock(side_effect=AssertionError("unexpected title generation"))
    github_token = AsyncMock(side_effect=AssertionError("unexpected GitHub token resolution"))
    monkeypatch.setattr(server, "schedule_thread_title_generation", title)
    monkeypatch.setattr(server, "resolve_github_token", github_token)
    middleware = server.PrepareAgentRunMiddleware(
        thread_id="eval-thread",
        config={
            "configurable": RunConfig(
                source="desktop",
                agent_eval=AgentEvaluationConfig(
                    base_commit="a" * 40,
                    checkout_dir=_CHECKOUT,
                ),
            ).dump()
        },
        profile_login=None,
        repo_instructions=None,
        model_id="test-model",
        effort=None,
        title_model=FakeMessagesListChatModel(responses=[]),
        source="desktop",
        user_email="",
        linear_project_id="",
        linear_issue_number="",
        draft_prs=False,
        plan_mode=False,
        admin_workspaces=False,
    )
    result = await middleware._prepare({"messages": []}, Runtime())
    assert Path(str(result["work_dir"])) == tmp_path
    title.assert_not_called()
    github_token.assert_not_awaited()


async def test_malformed_eval_configuration_cannot_fall_back_to_normal_execution() -> None:
    with pytest.raises(ValueError, match="Invalid agent_eval"):
        await server.get_agent(
            {
                "configurable": {
                    "__is_for_execution__": True,
                    "thread_id": "eval-thread",
                    "agent_eval": {
                        "base_commit": "HEAD; injected command",
                        "checkout_dir": _CHECKOUT,
                    },
                }
            }
        )
