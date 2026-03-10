from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.ensure_no_empty_msg import (
    check_if_confirming_completion,
    check_if_model_already_called_commit_and_open_pr,
    check_if_model_messaged_user,
    ensure_no_empty_msg,
    get_every_message_since_last_human,
)


class TestGetEveryMessageSinceLastHuman:
    def test_returns_messages_after_last_human(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="first human"),
                AIMessage(content="ai response"),
                HumanMessage(content="second human"),
                AIMessage(content="final ai"),
            ]
        }

        result = get_every_message_since_last_human(state)

        assert len(result) == 1
        assert result[0].content == "final ai"

    def test_returns_all_messages_when_no_human(self) -> None:
        state = {
            "messages": [
                AIMessage(content="ai 1"),
                AIMessage(content="ai 2"),
            ]
        }

        result = get_every_message_since_last_human(state)

        assert len(result) == 2
        assert result[0].content == "ai 1"
        assert result[1].content == "ai 2"

    def test_returns_empty_when_human_is_last(self) -> None:
        state = {
            "messages": [
                AIMessage(content="ai response"),
                HumanMessage(content="human last"),
            ]
        }

        result = get_every_message_since_last_human(state)

        assert len(result) == 0

    def test_returns_multiple_messages_after_human(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="human"),
                AIMessage(content="ai 1"),
                ToolMessage(content="tool result", tool_call_id="123"),
                AIMessage(content="ai 2"),
            ]
        }

        result = get_every_message_since_last_human(state)

        assert len(result) == 3
        assert result[0].content == "ai 1"
        assert result[1].content == "tool result"
        assert result[2].content == "ai 2"


class TestCheckIfModelAlreadyCalledCommitAndOpenPr:
    def test_returns_true_when_commit_and_open_pr_called(self) -> None:
        messages = [
            AIMessage(content="opening pr"),
            ToolMessage(content="PR opened", tool_call_id="123", name="commit_and_open_pr"),
        ]

        assert check_if_model_already_called_commit_and_open_pr(messages) is True

    def test_returns_false_when_not_called(self) -> None:
        messages = [
            AIMessage(content="doing something"),
            ToolMessage(content="done", tool_call_id="123", name="bash"),
        ]

        assert check_if_model_already_called_commit_and_open_pr(messages) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert check_if_model_already_called_commit_and_open_pr([]) is False

    def test_ignores_non_tool_messages(self) -> None:
        messages = [
            AIMessage(content="commit_and_open_pr"),
            HumanMessage(content="commit_and_open_pr"),
        ]

        assert check_if_model_already_called_commit_and_open_pr(messages) is False


class TestCheckIfModelMessagedUser:
    def test_returns_true_for_slack_thread_reply(self) -> None:
        messages = [
            ToolMessage(content="sent", tool_call_id="123", name="slack_thread_reply"),
        ]

        assert check_if_model_messaged_user(messages) is True

    def test_returns_true_for_linear_comment(self) -> None:
        messages = [
            ToolMessage(content="commented", tool_call_id="123", name="linear_comment"),
        ]

        assert check_if_model_messaged_user(messages) is True

    def test_returns_true_for_github_comment(self) -> None:
        messages = [
            ToolMessage(content="commented", tool_call_id="123", name="github_comment"),
        ]

        assert check_if_model_messaged_user(messages) is True

    def test_returns_false_for_other_tools(self) -> None:
        messages = [
            ToolMessage(content="result", tool_call_id="123", name="bash"),
            ToolMessage(content="result", tool_call_id="456", name="read_file"),
        ]

        assert check_if_model_messaged_user(messages) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert check_if_model_messaged_user([]) is False


class TestCheckIfConfirmingCompletion:
    def test_returns_true_when_confirming_completion_called(self) -> None:
        messages = [
            ToolMessage(content="confirmed", tool_call_id="123", name="confirming_completion"),
        ]

        assert check_if_confirming_completion(messages) is True

    def test_returns_false_for_other_tools(self) -> None:
        messages = [
            ToolMessage(content="result", tool_call_id="123", name="bash"),
        ]

        assert check_if_confirming_completion(messages) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert check_if_confirming_completion([]) is False

    def test_finds_confirming_completion_among_other_messages(self) -> None:
        messages = [
            AIMessage(content="working"),
            ToolMessage(content="done", tool_call_id="1", name="bash"),
            ToolMessage(content="confirmed", tool_call_id="2", name="confirming_completion"),
            AIMessage(content="finished"),
        ]

        assert check_if_confirming_completion(messages) is True


class TestEnsureNoEmptyMsgCommitAndNotify:
    """Tests the branch: commit_and_open_pr was called AND user was messaged -> return None."""

    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    def test_returns_none_when_pr_opened_and_user_messaged(self) -> None:
        empty_ai = AIMessage(content="")
        state = {
            "messages": [
                HumanMessage(content="fix the bug"),
                ToolMessage(content="PR opened", tool_call_id="1", name="commit_and_open_pr"),
                ToolMessage(content="message sent", tool_call_id="2", name="slack_thread_reply"),
                empty_ai,
            ]
        }

        result = ensure_no_empty_msg.after_model(state, self._make_runtime())

        assert result is None

    def test_returns_none_with_linear_comment_instead_of_slack(self) -> None:
        empty_ai = AIMessage(content="")
        state = {
            "messages": [
                HumanMessage(content="fix the bug"),
                ToolMessage(content="PR opened", tool_call_id="1", name="commit_and_open_pr"),
                ToolMessage(content="commented", tool_call_id="2", name="linear_comment"),
                empty_ai,
            ]
        }

        result = ensure_no_empty_msg.after_model(state, self._make_runtime())

        assert result is None

    def test_returns_none_with_github_comment_instead_of_slack(self) -> None:
        empty_ai = AIMessage(content="")
        state = {
            "messages": [
                HumanMessage(content="fix the bug"),
                ToolMessage(content="PR opened", tool_call_id="1", name="commit_and_open_pr"),
                ToolMessage(content="commented", tool_call_id="2", name="github_comment"),
                empty_ai,
            ]
        }

        result = ensure_no_empty_msg.after_model(state, self._make_runtime())

        assert result is None

    def test_injects_no_op_when_only_pr_opened_but_user_not_messaged(self) -> None:
        empty_ai = AIMessage(content="")
        state = {
            "messages": [
                HumanMessage(content="fix the bug"),
                ToolMessage(content="PR opened", tool_call_id="1", name="commit_and_open_pr"),
                empty_ai,
            ]
        }

        result = ensure_no_empty_msg.after_model(state, self._make_runtime())

        assert result is not None
        assert len(result["messages"]) == 2
        assert result["messages"][0].tool_calls[0]["name"] == "no_op"

    def test_injects_no_op_when_only_user_messaged_but_no_pr(self) -> None:
        empty_ai = AIMessage(content="")
        state = {
            "messages": [
                HumanMessage(content="fix the bug"),
                ToolMessage(content="message sent", tool_call_id="1", name="slack_thread_reply"),
                empty_ai,
            ]
        }

        result = ensure_no_empty_msg.after_model(state, self._make_runtime())

        assert result is not None
        assert len(result["messages"]) == 2
        assert result["messages"][0].tool_calls[0]["name"] == "no_op"
