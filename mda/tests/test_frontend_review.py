from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import agent
import httpx2
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import _ExecutionRuntime
from managed_deepagents import runtime as mda_runtime
from managed_deepagents.runtime import ManagedRunConfig, compile_managed_agent_async
from pydantic import JsonValue
from rt.remote_files import FILE_TOOLS
from runtime_api.catalog import Catalog, LoopRules, ModelSpec, RunModels
from runtime_api.frontend_review import (
    CapturedFrontendReview,
    FrontendScreenshot,
    SubmittedFrontendReview,
)
from runtime_api.reports import Reports


async def test_dedicated_review_reads_images_and_publishes_without_coding_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr(mda_runtime._SharedDefinitions, "_built", OrderedDict())
    monkeypatch.setattr(mda_runtime._SharedGraphs, "_built", OrderedDict())
    review_id = uuid4()
    image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jEioAAAAASUVORK5CYII="
    calls: list[str] = []
    offered: list[set[str]] = []
    model_calls = 0

    def bind(
        model: GenericFakeChatModel, tools: list[BaseTool], **kwargs: object
    ) -> GenericFakeChatModel:
        offered.append({tool.name for tool in tools})
        return model

    async def generate(
        model: GenericFakeChatModel, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        nonlocal model_calls
        model_calls += 1
        assert model_calls <= 2, messages[-1].text
        assert [m.text for m in messages if isinstance(m, SystemMessage)] == [
            "Assess only these screenshots."
        ]
        if model_calls == 1:
            call = ToolCall(
                name="read_frontend_review", id="inspect", args={"review_id": str(review_id)}
            )
        else:
            evidence = next(m for m in messages if isinstance(m, ToolMessage))
            images = [b for b in evidence.content_blocks if b["type"] == "image"]
            assert len(images) == 2
            assert all(b.get("url") == f"data:image/png;base64,{image}" for b in images)
            assert image not in evidence.text
            call = ToolCall(
                name="submit_frontend_review",
                id="publish-assessment",
                args={
                    "review_id": str(review_id),
                    "summary": "The layout changed.",
                    "annotations_by_scenario": {},
                },
            )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
        )

    model = GenericFakeChatModel(messages=iter([]))
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", bind)
    monkeypatch.setattr(GenericFakeChatModel, "_agenerate", generate)
    monkeypatch.setattr(agent, "build_model", lambda *args, **kwargs: model)
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: model)
    monkeypatch.setattr("rt.run_tools.build_model", lambda *args, **kwargs: model)
    spec = ModelSpec(model_id="openai:gpt-6.1-sol", kwargs={})
    catalog = Catalog(
        models=RunModels(model=spec, subagent_model=spec, use_gateway=False),
        system_prompt="Assess only these screenshots.",
        frontend_inspect_description="Read the evidence.",
        frontend_assessment_description="Publish the assessment.",
        code_interpreter=False,
        excluded=[
            *FILE_TOOLS,
            "write_todos",
            "task",
            "eval",
            "start_async_task",
            "check_async_task",
            "update_async_task",
            "cancel_async_task",
            "list_async_tasks",
        ],
        loop=LoopRules(stop_when=["frontend_review_complete"]),
    )
    request = httpx2.Request("POST", "https://backend.invalid")

    async def post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, JsonValue],
        **kwargs: object,
    ) -> httpx2.Response:
        hook = url.rsplit("/", 1)[-1]
        calls.append(hook)
        assert headers["Authorization"] == "Bearer run-token"
        if hook == "read_frontend_review":
            result = CapturedFrontendReview(
                review_id=str(review_id),
                status="complete",
                report_url=f"/reviews/run/{review_id}",
                screenshots=[
                    FrontendScreenshot(
                        scenario_id="page-0", role=role, width=1, height=1, png_base64=image
                    )
                    for role in ("before", "after")
                ],
            ).model_dump(mode="json")
        elif hook == "submit_frontend_review":
            result = SubmittedFrontendReview(
                review_id=review_id,
                report_url=f"/reviews/run/{review_id}",
            ).model_dump(mode="json")
        elif hook == "reports":
            result = {"replies": [{} for _ in Reports.model_validate(json).items]}
        else:
            result = {}
        return httpx2.Response(200, request=request, json=result)

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    config: ManagedRunConfig = {"configurable": {"thread_id": "visual-thread"}}
    context: dict[str, object] = {
        "graph": "reviewer",
        "__run_token": "run-token",
        "invocation_id": "visual-invocation",
        "__catalog": catalog.model_dump(mode="json"),
    }
    graph = await compile_managed_agent_async(
        agent.agent,
        config,
        runtime=_ExecutionRuntime(
            access_context="threads.create_run", store=InMemoryStore(), context=context
        ),
        report_telemetry=False,
        async_subagent_graphs={"general-purpose": "open-swe--general-purpose"},
    )
    result = await graph.ainvoke(
        {"messages": [("user", "Publish the reviewed evidence.")]},
        config,
        context=context,  # ty: ignore[invalid-argument-type]
    )
    assert isinstance(result["messages"][-1], ToolMessage)
    assert result["messages"][-1].name == "submit_frontend_review"
    assert result["messages"][-1].status == "success"
    assert model_calls == 2
    assert offered == [{"read_frontend_review", "submit_frontend_review"}] * 2
    assert calls.count("read_frontend_review") == 1
    assert calls.count("submit_frontend_review") == 1
    assert calls[-1] == "run_ended"
    assert set(calls) <= {
        "prepare",
        "reports",
        "model_result",
        "tool_started",
        "tool_result",
        "read_frontend_review",
        "submit_frontend_review",
        "run_ended",
    }
