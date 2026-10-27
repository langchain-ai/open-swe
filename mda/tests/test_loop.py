from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    messages_from_dict,
)
from pydantic import BaseModel, JsonValue
from rt.backend import BackendCallError, OpenSweBackend, Run, RunContext
from rt.loop import RunLoop
from rt.middleware import BackendRunMiddleware
from rt.tools import STATE_KEY
from runtime_api.catalog import (
    CLIENT_CALL_PENDING,
    Catalog,
    LoopRules,
    ModelSpec,
    ReplyRules,
    RoutingRules,
    RunModels,
)
from runtime_api.endpoint import Delivery
from runtime_api.loop import Done, GitHubProxyRefreshed, PhaseResult, Route, TaskContext
from runtime_api.reports import REPORTS, Reports, ReportsTaken

_MAIN = ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={})
_FALLBACK = ModelSpec(model_id="openai:gpt-6.1-sol", kwargs={})
_FAST = ModelSpec(model_id="fireworks:kimi", kwargs={}, reads_images=False, fallback=_FALLBACK)
_VISION = ModelSpec(model_id="google_genai:gemini-3.8-flash", kwargs={})
_MODELS = RunModels(
    model=_MAIN, subagent_model=_MAIN, use_gateway=True, routes={"fast": _FAST}, vision=_VISION
)
_GUARD = "system:reply-guard"
_NUDGE = (
    f'<input-message sender="{_GUARD}" surface="automation" kind="system">'
    "Reply to the person.</input-message>"
)
_REPLY = ReplyRules(
    tool="slack_reply",
    no_reply_tool="slack_no_reply_needed",
    nudge=[_NUDGE],
    nudge_sender=_GUARD,
)

_TOKEN_EXPIRES = datetime(2999, 1, 1, 12, tzinfo=UTC)

type Calls = list[tuple[str, dict[str, JsonValue]]]


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    answers: Mapping[str, BaseModel | BackendCallError],
    rules: LoopRules | None = None,
) -> tuple[OpenSweBackend, Calls]:
    """A run's backend answering each named call with ``answers``, or failing it, and the
    calls it was asked; the run's catalog has ``rules`` for its loop."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.loop.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    catalog = Catalog() if rules is None else Catalog(loop=rules, models=_MODELS)
    context = {"__run_token": "token", "__catalog": catalog.model_dump(mode="json")}
    backend = Run(RunContext.model_validate(context)).backend
    calls: Calls = []

    async def post(
        path: str,
        body: dict[str, JsonValue],
        *,
        timeout: float,
        delivery: Delivery,
        headers: Mapping[str, str] | None = None,
    ) -> JsonValue:
        name = path.rsplit("/", 1)[-1]
        if name == REPORTS.name:
            return (await _taking(body)).model_dump(mode="json")
        calls.append((name, body))
        answer = answers[name]
        if isinstance(answer, BackendCallError):
            raise answer
        return answer.model_dump(mode="json")

    async def _taking(body: dict[str, JsonValue]) -> ReportsTaken:
        """Each item of a batch, answered as the call it stands for."""
        replies: list[dict[str, JsonValue]] = []
        for item in Reports.model_validate(body).items:
            calls.append((item.endpoint, item.request.model_dump(mode="json")))
            answer = answers[item.endpoint]
            if isinstance(answer, BackendCallError):
                return ReportsTaken(replies=replies, refused=str(answer))
            replies.append(answer.model_dump(mode="json"))
        return ReportsTaken(replies=replies)

    monkeypatch.setattr(backend, "post", post)
    return backend, calls


def _state(*messages: BaseMessage, **held: JsonValue) -> dict[str, object]:
    return {"messages": list(messages), STATE_KEY: held}


async def test_a_failed_github_token_refresh_waits_before_the_next_try(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, calls = _backend(
        monkeypatch,
        {"refresh_github_proxy": BackendCallError("refresh_github_proxy failed: timed out")},
        LoopRules(refresh_github_proxy=True),
    )
    loop = RunLoop(backend)

    first = await loop.before_model(_state(HumanMessage(content="Fix the login", id="human-1")))
    held = {"github_proxy_refresh_at": first.update["github_proxy_refresh_at"]}
    second = await loop.before_model(
        _state(HumanMessage(content="Fix the login", id="human-1"), **held)
    )

    assert first.call is not None and second.call is not None
    assert [name for name, _ in calls] == ["refresh_github_proxy"]


async def test_each_model_call_is_assembled_here_from_the_rules_and_named_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = '<dynamic-context kind="task" id="task-1">Fix login</dynamic-context>'
    rules = LoopRules(
        deployment_prompt="This is a preview deployment.",
        hidden_tools=["grep"],
        hidden_unless={"has_human_input": ["record_human_input"]},
        routing=RoutingRules(mode="fast", route_model_ids={"fast": _FAST.model_id}),
        review_guide=True,
        task_coordination=True,
        refresh_github_proxy=True,
    )
    backend, calls = _backend(
        monkeypatch,
        {
            "task_context": TaskContext(instructions="You coordinate the task.", message=context),
            "refresh_github_proxy": GitHubProxyRefreshed(expires_at=_TOKEN_EXPIRES),
        },
        rules,
    )
    loop = RunLoop(backend)
    state = _state(
        HumanMessage(content="Fix the login", id="human-1"),
        rendered_system_prompt="You are Open SWE.",
        review_guide_rules="Walk the reviewer through it.",
    )

    planned = await loop.before_model(state)
    await backend.outbox.drain()

    assert planned.call is not None
    assert planned.call.system_prompt == (
        "This is a preview deployment.\n\nYou are Open SWE.\n\n"
        "Walk the reviewer through it.\n\nYou coordinate the task."
    )
    assert planned.call.hidden_tools == ["grep", "record_human_input"]
    assert planned.update == {
        "github_proxy_refresh_at": "2999-01-01T11:50:00+00:00",
        "model_route": "fast",
    }
    # The fast route's model reads no images, so a call with one goes to the vision model.
    assert (planned.call.model, planned.call.vision) == (_FAST, _VISION)
    assert planned.call.fallback == _FALLBACK
    assert planned.call.use_gateway is True
    assert [message.content for message in messages_from_dict(planned.messages)] == [context]
    assert sorted(name for name, _ in calls) == ["refresh_github_proxy", "task_context"]

    # The task's context the model still sees is not added again, and a token with time
    # left is not replaced.
    again = await loop.before_model(
        _state(
            HumanMessage(content=context, id="task-context:1"),
            model_route="fast",
            github_proxy_refresh_at=planned.update["github_proxy_refresh_at"],
        )
    )
    assert again.messages == []
    assert [name for name, _ in calls].count("refresh_github_proxy") == 1


async def test_a_requested_model_is_asked_for_once_and_the_classifier_routes_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = ModelSpec(model_id="openai:gpt-6.1-sol", kwargs={"reasoning_effort": "high"})
    backend, calls = _backend(
        monkeypatch,
        {"model_choice": requested, "route_model": Route(route="default")},
        LoopRules(routing=RoutingRules(mode="fast")),
    )
    events: list[object] = []
    monkeypatch.setattr("rt.loop.get_stream_writer", lambda: events.append)
    loop = RunLoop(backend)
    asked = _state(
        HumanMessage(content="Use GPT", id="human-1"),
        requested_model=requested.model_id,
        requested_effort="high",
    )

    first = await loop.before_model(asked)
    second = await loop.before_model(asked)

    assert first.call is not None and first.call.model == requested
    assert second.call is not None and second.call.model == requested
    assert first.update == {"model_route": "default"}
    # The dashboard shows the model the person asked for next to the route.
    assert events[-1] == {
        "type": "model_routed",
        "route": "default",
        "model_id": requested.model_id,
    }
    assert calls == [
        (
            "model_choice",
            {"run_id": "run-1", "model_id": requested.model_id, "effort": "high"},
        )
    ]


@pytest.mark.parametrize(
    "state",
    [
        _state(HumanMessage(content="Plan", id="human-1"), scout_nothing_to_plan=True),
        _state(
            AIMessage(content="", tool_calls=[{"name": "ask", "args": {}, "id": "call-1"}]),
            ToolMessage(
                content="waiting", tool_call_id="call-1", artifact={CLIENT_CALL_PENDING: True}
            ),
        ),
    ],
)
async def test_a_run_ends_before_its_next_call_where_a_rule_says_so(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, object]
) -> None:
    rules = LoopRules(stop_when=["scout_nothing_to_plan"], client_tools=True)
    backend, calls = _backend(monkeypatch, {}, rules)

    planned = await RunLoop(backend).before_model(state)

    assert planned.jump_to == "end"
    assert calls == []


async def test_an_answer_that_owes_a_slack_reply_is_asked_again_then_posted_for_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, calls = _backend(monkeypatch, {"reply_on_behalf": Done()}, LoopRules(reply=_REPLY))
    loop = RunLoop(backend)
    asked = HumanMessage(content="Fix it", id="human-1")
    answer = AIMessage(content="Fixed the login.", id="ai-1")

    nudged = await loop.after_model(_state(asked, answer, reply_surface="slack"))
    posted = await loop.after_model(
        _state(asked, answer, reply_surface="slack", reply_nudges=_REPLY.max_nudges)
    )
    on_the_web = await loop.after_model(_state(asked, answer, reply_surface="web"))

    assert nudged.jump_to == "model"
    assert nudged.update == {"reply_nudges": 1}
    assert [message.content for message in messages_from_dict(nudged.messages)] == [_NUDGE]
    assert posted.jump_to is None and posted.update == {"reply_nudges": 0}
    assert [(name, body["text"]) for name, body in calls] == [
        ("reply_on_behalf", "Fixed the login.")
    ]
    assert on_the_web == PhaseResult()


@pytest.mark.parametrize(
    ("replied", "result", "satisfied"),
    [
        ("slack_reply", {"success": True}, True),
        ("slack_reply", {"success": False}, False),
        ("slack_no_reply_needed", {"success": True}, True),
        # A tool that says a completion reply is owed rules out "no reply needed".
        ("slack_no_reply_needed", {"success": True, "completion_reply_required": True}, False),
    ],
)
async def test_a_reply_this_turn_satisfies_the_run_even_after_a_nudge(
    monkeypatch: pytest.MonkeyPatch, replied: str, result: dict[str, bool], satisfied: bool
) -> None:
    import json

    backend, _calls = _backend(monkeypatch, {}, LoopRules(reply=_REPLY))
    loop = RunLoop(backend)
    args = {"response_type": "final"} if replied == "slack_reply" else {}
    state = _state(
        HumanMessage(content="Fix it", id="human-1"),
        AIMessage(content="", tool_calls=[{"name": replied, "args": args, "id": "reply-1"}]),
        ToolMessage(content=json.dumps(result), tool_call_id="reply-1"),
        AIMessage(content="Anything else?", id="ai-1"),
        # The guard's own nudge starts no turn of its own.
        HumanMessage(content=_NUDGE, id="nudge-1"),
        AIMessage(content="Done.", id="ai-2"),
        reply_surface="slack",
    )

    answered = await loop.after_model(state)

    assert (answered.jump_to is None) is satisfied


async def test_the_end_of_a_run_is_reported_and_its_graph_work_done_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, calls = _backend(
        monkeypatch,
        {
            "prepare": PhaseResult(update={"rendered_system_prompt": "You review."}),
            # Neither a report nor a notice that fails takes the review check's settling with it.
            "run_ended": BackendCallError("run_ended failed: timed out"),
            "step_limit_reached": BackendCallError("step_limit_reached failed: Slack is down"),
            "settle_review_check": Done(),
        },
        LoopRules(at_end=["settle_review_check"]),
    )
    monkeypatch.setattr(
        "rt.middleware.build_model",
        lambda spec, use_gateway: FakeListChatModel(responses=["Model call limits exceeded"]),
    )
    monkeypatch.setattr("rt.outbox._RETRY_SECONDS", (0.0,))
    backend.run.invocation_id = "turn"
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=[BackendRunMiddleware(backend)],
    )

    await graph.ainvoke({"messages": [("user", "Review it")]})
    await backend.outbox.drain()

    # Only preparation is a phase call: the loop and the end of the run are named calls.
    # The graph's own work goes out before the run's end, in the order asked for, and
    # work the backend keeps failing is tried again, then let go of for the rest.
    assert [name for name, _ in calls] == [
        "prepare",
        "step_limit_reached",
        "step_limit_reached",
        "step_limit_reached",
        "settle_review_check",
        "run_ended",
    ]


async def test_reports_carry_what_they_report_never_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import ToolCall
    from langchain_core.tools import tool
    from rt.middleware import ReportRepliesMiddleware, RuntimeToolsMiddleware
    from runtime_api.loop import ModelResult, ToolResultReply

    backend, calls = _backend(
        monkeypatch,
        {
            "model_result": Done(),
            "tool_started": Done(),
            "tool_result": ToolResultReply(),
        },
    )
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    backend.run.invocation_id = "invocation-1"

    @tool
    async def look() -> str:
        """Look around."""
        return "seen"

    called = AIMessage(
        content="", id="ai-1", tool_calls=[ToolCall(name="look", args={}, id="call-1")]
    )
    graph = create_agent(
        model=GenericFakeChatModel(messages=iter([called, AIMessage(content="done", id="ai-2")])),
        tools=[look],
        middleware=[
            ReportRepliesMiddleware(backend),
            RuntimeToolsMiddleware(backend, ()),
        ],
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="hello", id="human-1")]},
        {"configurable": {"thread_id": "thread-1"}},
    )
    await backend.outbox.drain()

    # The run's usage counts its own replies by the invocation stamped on them here.
    assert [
        message.response_metadata.get("open_swe_invocation_id")
        for message in result["messages"]
        if isinstance(message, AIMessage)
    ] == ["invocation-1", "invocation-1"]

    assert all("messages" not in body and "state" not in body for _, body in calls)
    replies = [ModelResult.model_validate(body) for name, body in calls if name == "model_result"]
    # Only what the model had not answered yet: the person's message, then nothing new.
    assert [[human.id for human in messages_from_dict(r.humans)] for r in replies] == [
        ["human-1"],
        [],
    ]
    [started] = [body for name, body in calls if name == "tool_started"]
    assert started["message_id"] == "ai-1"
