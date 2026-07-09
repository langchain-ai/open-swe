from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.failed_delivery_guard import escalate_failed_final_delivery


def _runtime() -> MagicMock:
    return MagicMock()


def _delivery_messages(error: str, *, tool: str = "slack_thread_reply") -> list[object]:
    args = (
        {"message": "Done: opened draft PR"}
        if tool == "slack_thread_reply"
        else {
            "comment_body": "Done: opened draft PR",
            "ticket_id": "abc",
        }
    )
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": tool, "args": args}],
    )
    tool_msg = ToolMessage(
        content=json.dumps(
            {"success": False, "error": error, "hint": "do not retry; use a fallback surface"}
        ),
        name=tool,
        tool_call_id="call_1",
    )
    return [HumanMessage(content="ship it"), ai, tool_msg]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", ["not_in_channel", "channel_not_found", "missing_slack_bot_token"]
)
async def test_escalates_when_final_delivery_failed(error: str) -> None:
    state = {"messages": _delivery_messages(error)}

    with patch(
        "agent.middleware.failed_delivery_guard.report_platform_issue",
        new_callable=AsyncMock,
        return_value={"report_id": "rid"},
    ) as mock_report:
        result = await escalate_failed_final_delivery.aafter_agent(state, _runtime())

    assert result is None
    mock_report.assert_awaited_once()
    kwargs = mock_report.await_args.kwargs
    assert kwargs["summary"] == "Done: opened draft PR"
    assert kwargs["hint"] == "do not retry; use a fallback surface"


@pytest.mark.asyncio
async def test_escalates_for_linear_comment_failure() -> None:
    state = {"messages": _delivery_messages("channel_not_found", tool="linear_comment")}

    with patch(
        "agent.middleware.failed_delivery_guard.report_platform_issue",
        new_callable=AsyncMock,
        return_value={"report_id": "rid"},
    ) as mock_report:
        await escalate_failed_final_delivery.aafter_agent(state, _runtime())

    mock_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_when_final_delivery_succeeded() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "slack_thread_reply", "args": {"message": "ok"}}],
    )
    tool_msg = ToolMessage(
        content=json.dumps({"success": True}), name="slack_thread_reply", tool_call_id="call_1"
    )
    state = {"messages": [ai, tool_msg]}

    with patch(
        "agent.middleware.failed_delivery_guard.report_platform_issue",
        new_callable=AsyncMock,
    ) as mock_report:
        await escalate_failed_final_delivery.aafter_agent(state, _runtime())

    mock_report.assert_not_called()


@pytest.mark.asyncio
async def test_skips_for_non_escalating_error() -> None:
    state = {"messages": _delivery_messages("msg_too_long")}

    with patch(
        "agent.middleware.failed_delivery_guard.report_platform_issue",
        new_callable=AsyncMock,
    ) as mock_report:
        await escalate_failed_final_delivery.aafter_agent(state, _runtime())

    mock_report.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_last_message_not_delivery_tool() -> None:
    state = {"messages": [AIMessage(content="all done")]}

    with patch(
        "agent.middleware.failed_delivery_guard.report_platform_issue",
        new_callable=AsyncMock,
    ) as mock_report:
        await escalate_failed_final_delivery.aafter_agent(state, _runtime())

    mock_report.assert_not_called()
