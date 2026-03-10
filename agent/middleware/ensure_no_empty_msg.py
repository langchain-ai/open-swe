from typing import Any
from uuid import uuid4

from langchain.agents.middleware import AgentState, after_model
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.runtime import Runtime


def get_every_message_since_last_human(state: AgentState) -> list[AnyMessage]:
    messages = state["messages"]
    last_human_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].type == "human":
            last_human_idx = i
            break
    return messages[last_human_idx + 1 :]


def check_if_model_already_called_commit_and_open_pr(messages: list[AnyMessage]) -> bool:
    for msg in messages:
        if msg.type == "tool" and msg.name == "commit_and_open_pr":
            return True
    return False


def check_if_model_messaged_user(messages: list[AnyMessage]) -> bool:
    for msg in messages:
        if msg.type == "tool" and msg.name in [
            "slack_thread_reply",
            "linear_comment",
            "github_comment",
        ]:
            return True
    return False


def check_if_confirming_completion(messages: list[AnyMessage]) -> bool:
    for msg in messages:
        if msg.type == "tool" and msg.name == "confirming_completion":
            return True
    return False


def check_if_no_op(messages: list[AnyMessage]) -> bool:
    for msg in messages:
        if msg.type == "tool" and msg.name == "no_op":
            return True
    return False


@after_model
def ensure_no_empty_msg(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    last_msg = state["messages"][-1]
    has_contents = bool(last_msg.text())
    has_tool_calls = bool(last_msg.tool_calls)
    if not has_tool_calls and not has_contents:
        messages_since_last_human = get_every_message_since_last_human(state)
        if check_if_no_op(messages_since_last_human):
            return None

        if check_if_model_already_called_commit_and_open_pr(
            messages_since_last_human
        ) and check_if_model_messaged_user(messages_since_last_human):
            return None

        tc_id = str(uuid4())
        last_msg.tool_calls = [{"name": "no_op", "args": {}, "id": tc_id}]
        no_op_tool_msg = ToolMessage(
            content="No operation performed."
            + "Please continue with the task, ensuring you ALWAYS call at least one tool in"
            + " every message unless you are absolutely sure the task has been fully completed.",
            tool_call_id=tc_id,
        )

        return {"messages": [last_msg, no_op_tool_msg]}

    if has_contents and not has_tool_calls:
        # See if the model already called open_pr or it sent a slack/linear message
        # First, get every message since the last human message
        messages_since_last_human = get_every_message_since_last_human(state)

        # If it opened a PR, we don't need to do anything
        if (
            check_if_model_already_called_commit_and_open_pr(messages_since_last_human)
            or check_if_model_messaged_user(messages_since_last_human)
            or check_if_confirming_completion(messages_since_last_human)
        ):
            return None

        tc_id = str(uuid4())
        last_msg.tool_calls = [{"name": "confirming_completion", "args": {}, "id": tc_id}]
        no_op_tool_msg = ToolMessage(
            content="Confirming task completion. I see you did not call a tool, which would end the task, however you haven't called a tool to message the user or open a pull request."
            + "This may indicate premature termination - please ensure you fully complete the task before ending it. "
            + "If you do not call any tools it will end the task.",
            name="confirming_completion",
            tool_call_id=tc_id,
        )

        return {"messages": [last_msg, no_op_tool_msg]}

    return None
