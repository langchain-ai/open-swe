"""
Eval tests for the ensure_no_empty_msg middleware fix.

These tests capture the bug where Branch 1 (empty AI message with no content
and no tool calls) was injecting no_op even after commit_and_open_pr AND
user notification had already occurred.

Bug trace URLs (production):
- https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/projects/p/6a5cf28f-7c41-4ee9-a11e-696c74ddb5f6/r/019cd478-c11c-7f02-b895-bfcbc077f305
- https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/projects/p/6a5cf28f-7c41-4ee9-a11e-696c74ddb5f6/r/019cd007-d08b-75a0-9a16-25d61a5196f1
- https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/projects/p/6a5cf28f-7c41-4ee9-a11e-696c74ddb5f6/r/019cceea-d7e4-7443-8396-24c6db08f078
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langsmith import testing as t

from agent.middleware.ensure_no_empty_msg import (
    ensure_no_empty_msg,
)


def _log_inputs(data: dict) -> None:
    """Log inputs to langsmith if tracing is enabled, otherwise no-op."""
    try:
        t.log_inputs(data)
    except ValueError:
        pass


def _log_outputs(data: dict) -> None:
    """Log outputs to langsmith if tracing is enabled, otherwise no-op."""
    try:
        t.log_outputs(data)
    except ValueError:
        pass


def _make_state(messages):
    """Helper to create an AgentState-like dict."""
    return {"messages": messages}


def _empty_ai_message():
    """Return an AIMessage with no content and no tool calls."""
    msg = AIMessage(content="")
    msg.tool_calls = []
    return msg


def _pr_tool_message(tool_call_id="tc-pr"):
    return ToolMessage(
        content="PR opened successfully", tool_call_id=tool_call_id, name="commit_and_open_pr"
    )


def _slack_tool_message(tool_call_id="tc-slack"):
    return ToolMessage(content="Message sent", tool_call_id=tool_call_id, name="slack_thread_reply")


def _linear_tool_message(tool_call_id="tc-linear"):
    return ToolMessage(content="Comment posted", tool_call_id=tool_call_id, name="linear_comment")


def _github_comment_tool_message(tool_call_id="tc-gh"):
    return ToolMessage(content="Comment posted", tool_call_id=tool_call_id, name="github_comment")


@pytest.mark.langsmith
def test_branch1_returns_none_when_pr_opened_and_user_messaged_via_slack():
    """
    BUG TEST (Branch 1): Empty AI message after commit_and_open_pr + slack_thread_reply
    should return None (allow termination), NOT inject no_op.

    This test FAILS before the fix and PASSES after.
    """
    empty_ai = _empty_ai_message()
    state = _make_state(
        [
            HumanMessage(content="Please implement feature X and open a PR"),
            AIMessage(content="I'll implement the feature."),
            ToolMessage(content="done", tool_call_id="tc-bash", name="bash"),
            AIMessage(
                content="", tool_calls=[{"name": "commit_and_open_pr", "args": {}, "id": "tc-pr1"}]
            ),
            _pr_tool_message("tc-pr1"),
            AIMessage(
                content="",
                tool_calls=[{"name": "slack_thread_reply", "args": {}, "id": "tc-slack1"}],
            ),
            _slack_tool_message("tc-slack1"),
            empty_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Empty AI message after commit_and_open_pr + slack_thread_reply",
            "last_message_type": "AIMessage",
            "last_message_content": "",
            "last_message_tool_calls": [],
            "pr_opened": True,
            "user_messaged": True,
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "expected": "None",
            "is_none": result is None,
        }
    )

    assert result is None, (
        f"Expected None (allow termination) but got {result}. "
        "Branch 1 should return None when both commit_and_open_pr and user notification have occurred."
    )


@pytest.mark.langsmith
def test_branch1_returns_none_when_pr_opened_and_user_messaged_via_linear():
    """
    BUG TEST (Branch 1): Empty AI message after commit_and_open_pr + linear_comment
    should return None (allow termination), NOT inject no_op.
    """
    empty_ai = _empty_ai_message()
    state = _make_state(
        [
            HumanMessage(content="Fix the bug and open a PR"),
            AIMessage(
                content="", tool_calls=[{"name": "commit_and_open_pr", "args": {}, "id": "tc-pr1"}]
            ),
            _pr_tool_message("tc-pr1"),
            AIMessage(
                content="", tool_calls=[{"name": "linear_comment", "args": {}, "id": "tc-lin1"}]
            ),
            _linear_tool_message("tc-lin1"),
            empty_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Empty AI message after commit_and_open_pr + linear_comment",
            "pr_opened": True,
            "user_messaged_via": "linear_comment",
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
        }
    )

    assert result is None, (
        f"Expected None (allow termination) but got {result}. "
        "Branch 1 should return None when both commit_and_open_pr and linear_comment have occurred."
    )


@pytest.mark.langsmith
def test_branch1_returns_none_when_pr_opened_and_user_messaged_via_github_comment():
    """
    BUG TEST (Branch 1): Empty AI message after commit_and_open_pr + github_comment
    should return None (allow termination), NOT inject no_op.
    """
    empty_ai = _empty_ai_message()
    state = _make_state(
        [
            HumanMessage(content="Fix and comment on the PR"),
            AIMessage(
                content="", tool_calls=[{"name": "commit_and_open_pr", "args": {}, "id": "tc-pr1"}]
            ),
            _pr_tool_message("tc-pr1"),
            AIMessage(
                content="", tool_calls=[{"name": "github_comment", "args": {}, "id": "tc-gh1"}]
            ),
            _github_comment_tool_message("tc-gh1"),
            empty_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Empty AI message after commit_and_open_pr + github_comment",
            "pr_opened": True,
            "user_messaged_via": "github_comment",
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
        }
    )

    assert result is None, (
        f"Expected None (allow termination) but got {result}. "
        "Branch 1 should return None when both commit_and_open_pr and github_comment have occurred."
    )


@pytest.mark.langsmith
def test_branch1_injects_no_op_when_only_user_messaged_no_pr():
    """
    Mid-task status update: Empty AI message after only user messaging (no PR opened)
    should inject no_op to force continuation.

    This should PASS both before and after the fix (existing correct behavior).
    """
    empty_ai = _empty_ai_message()
    state = _make_state(
        [
            HumanMessage(content="Implement feature X"),
            AIMessage(content="I'll start working on it."),
            AIMessage(
                content="",
                tool_calls=[{"name": "slack_thread_reply", "args": {}, "id": "tc-slack1"}],
            ),
            _slack_tool_message("tc-slack1"),
            empty_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Empty AI message after slack_thread_reply but NO commit_and_open_pr",
            "pr_opened": False,
            "user_messaged": True,
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
            "injected_no_op": result is not None,
        }
    )

    assert result is not None, (
        "Expected no_op injection (mid-task, no PR yet) but got None. "
        "Branch 1 should still inject no_op when only user was messaged but no PR was opened."
    )
    # Check that no_op was actually injected
    messages_in_result = result.get("messages", [])
    tool_names = [
        msg.tool_calls[0]["name"]
        for msg in messages_in_result
        if hasattr(msg, "tool_calls") and msg.tool_calls
    ]
    assert "no_op" in tool_names, f"Expected 'no_op' tool call but found: {tool_names}"


@pytest.mark.langsmith
def test_branch1_injects_no_op_when_only_pr_opened_no_user_message():
    """
    Empty AI message after commit_and_open_pr but WITHOUT user messaging
    should inject no_op (to force user notification before termination).

    This should PASS both before and after the fix (correct behavior preserved).
    """
    empty_ai = _empty_ai_message()
    state = _make_state(
        [
            HumanMessage(content="Open a PR for feature X"),
            AIMessage(
                content="", tool_calls=[{"name": "commit_and_open_pr", "args": {}, "id": "tc-pr1"}]
            ),
            _pr_tool_message("tc-pr1"),
            empty_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Empty AI message after commit_and_open_pr but NO user messaging",
            "pr_opened": True,
            "user_messaged": False,
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
            "injected_no_op": result is not None,
        }
    )

    assert result is not None, (
        "Expected no_op injection (PR opened but user not yet notified) but got None. "
        "Branch 1 should still inject no_op when PR was opened but user was not messaged."
    )


@pytest.mark.langsmith
def test_branch1_injects_no_op_when_no_completion_signals():
    """
    Existing behavior: empty AI message with no PR and no user message should
    inject no_op to force continuation.

    This should PASS both before and after the fix.
    """
    empty_ai = _empty_ai_message()
    state = _make_state(
        [
            HumanMessage(content="Write some code"),
            AIMessage(content="I'll write the code."),
            ToolMessage(content="done", tool_call_id="tc1", name="bash"),
            empty_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Empty AI message with no completion signals at all",
            "pr_opened": False,
            "user_messaged": False,
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
        }
    )

    assert result is not None, (
        "Expected no_op injection for empty message with no completion signals but got None."
    )


@pytest.mark.langsmith
def test_branch1_returns_none_when_no_op_already_present():
    """
    Existing behavior: if no_op was already injected once, Branch 1 should return None
    to avoid infinite no_op loops.

    This should PASS both before and after the fix.
    """
    empty_ai = _empty_ai_message()
    state = _make_state(
        [
            HumanMessage(content="Do something"),
            AIMessage(content="", tool_calls=[{"name": "no_op", "args": {}, "id": "tc-noop1"}]),
            ToolMessage(content="No operation performed.", tool_call_id="tc-noop1", name="no_op"),
            empty_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Empty AI message after a previous no_op was already injected",
            "prior_no_op": True,
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
        }
    )

    assert result is None, f"Expected None (no_op already present, avoid loop) but got {result}."


@pytest.mark.langsmith
def test_branch2_returns_none_when_user_messaged_text_only_ai_message():
    """
    Existing behavior (Branch 2): AI message with text but no tool calls,
    after user was messaged, should return None.

    This should PASS both before and after the fix (regression check).
    """
    text_ai = AIMessage(content="I have completed the task and opened a PR.")
    text_ai.tool_calls = []

    state = _make_state(
        [
            HumanMessage(content="Implement feature X"),
            AIMessage(
                content="",
                tool_calls=[{"name": "slack_thread_reply", "args": {}, "id": "tc-slack1"}],
            ),
            _slack_tool_message("tc-slack1"),
            text_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Text-only AI message after user was messaged (Branch 2)",
            "last_message_has_content": True,
            "last_message_has_tool_calls": False,
            "user_messaged": True,
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
        }
    )

    assert result is None, f"Expected None for Branch 2 when user was messaged but got {result}."


@pytest.mark.langsmith
def test_branch2_returns_none_when_pr_opened_text_only_ai_message():
    """
    Existing behavior (Branch 2): AI message with text but no tool calls,
    after PR was opened, should return None.

    This should PASS both before and after the fix (regression check).
    """
    text_ai = AIMessage(content="I have completed the task.")
    text_ai.tool_calls = []

    state = _make_state(
        [
            HumanMessage(content="Open a PR"),
            AIMessage(
                content="", tool_calls=[{"name": "commit_and_open_pr", "args": {}, "id": "tc-pr1"}]
            ),
            _pr_tool_message("tc-pr1"),
            text_ai,
        ]
    )

    _log_inputs(
        {
            "description": "Text-only AI message after PR opened (Branch 2)",
            "last_message_has_content": True,
            "pr_opened": True,
        }
    )

    result = ensure_no_empty_msg.after_model(state, None)

    _log_outputs(
        {
            "result": str(result),
            "is_none": result is None,
        }
    )

    assert result is None, f"Expected None for Branch 2 when PR was opened but got {result}."
