"""Break runaway write_todos re-planning loops that never advance the task."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_comment
from ..utils.github_token import get_github_token
from ..utils.linear import comment_on_linear_issue
from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

# The no-progress planning tool this guard watches. Kept in sync with the
# offline write_todos_loop / max_consecutive_write_todos evaluator so the
# runtime guard and the metric count the same signal.
NO_PROGRESS_TOOL = "write_todos"

# Any of these breaking the write_todos streak means the model made real
# progress (or communicated with the developer), so the counter resets.
STATE_CHANGING_TOOLS = frozenset(
    {
        "edit_file",
        "write_file",
        "execute",
        "open_pull_request",
        "request_pr_review",
        "slack_thread_reply",
        "slack_start_new_thread",
        "linear_comment",
    }
)

DEFAULT_SOFT_THRESHOLD = 5
DEFAULT_HARD_CEILING = 10

_GUARD_MARKER = "write_todos loop guard triggered"
_NUDGE_MARKER = "write_todos loop guard nudge"
_HARD_STOP_MESSAGE = (
    "I kept re-planning with write_todos without taking any concrete action, so I "
    "stopped instead of burning the whole budget. Please retrigger with a more "
    "focused request or tell me the specific next step to take."
)
_NUDGE_INSTRUCTION = (
    "<planning_loop_warning>\n"
    "You have called write_todos {count} times in a row without editing a file, "
    "running a command, opening a PR, or replying in the source channel. Stop "
    "re-planning: either take a concrete action now, or post a slack_thread_reply "
    "summarizing the blocker so the developer is notified.\n"
    "</planning_loop_warning>"
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping):
            text = block.get("text", "")
            parts.append(text if isinstance(text, str) else str(text))
        else:
            parts.append(str(block))
    return " ".join(parts)


def _tool_names(message: BaseMessage) -> list[str]:
    tool_calls = getattr(message, "tool_calls", None) or []
    names: list[str] = []
    for call in tool_calls:
        if isinstance(call, Mapping):
            name = call.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def consecutive_write_todos_count(messages: Sequence[BaseMessage]) -> int:
    """Count trailing AI turns whose only tool call is write_todos."""
    count = 0
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        names = _tool_names(message)
        if not names:
            continue
        if all(name == NO_PROGRESS_TOOL for name in names):
            count += 1
            continue
        break
    return count


def _get_slack_target(configurable: Mapping[str, Any]) -> tuple[str, str] | None:
    slack_thread = configurable.get("slack_thread")
    if not isinstance(slack_thread, Mapping):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return None
    if not channel_id or not thread_ts:
        return None
    return channel_id, thread_ts


def _get_linear_issue_id(configurable: Mapping[str, Any]) -> str | None:
    linear_issue = configurable.get("linear_issue")
    if not isinstance(linear_issue, Mapping):
        return None
    issue_id = linear_issue.get("id")
    return issue_id if isinstance(issue_id, str) and issue_id else None


def _coerce_issue_number(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _get_github_target(configurable: Mapping[str, Any]) -> tuple[dict[str, str], int] | None:
    repo_config = configurable.get("repo")
    if not isinstance(repo_config, Mapping):
        return None
    owner = repo_config.get("owner")
    name = repo_config.get("name")
    if not isinstance(owner, str) or not isinstance(name, str) or not owner or not name:
        return None
    repo = {"owner": owner, "name": name}

    github_issue = configurable.get("github_issue")
    if isinstance(github_issue, Mapping):
        number = _coerce_issue_number(github_issue.get("number"))
        if number is not None:
            return repo, number

    pr_number = _coerce_issue_number(configurable.get("pr_number"))
    if pr_number is not None:
        return repo, pr_number
    return None


async def _post_closeout(config: Mapping[str, Any]) -> None:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return

    slack_target = _get_slack_target(configurable)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        await post_slack_thread_reply(channel_id, thread_ts, _HARD_STOP_MESSAGE)
        logger.info("Sent write_todos loop closeout to Slack thread %s", thread_ts)
        return

    linear_issue_id = _get_linear_issue_id(configurable)
    if linear_issue_id is not None:
        await comment_on_linear_issue(linear_issue_id, _HARD_STOP_MESSAGE)
        logger.info("Sent write_todos loop closeout to Linear issue %s", linear_issue_id)
        return

    github_target = _get_github_target(configurable)
    if github_target is not None:
        token = get_github_token(config) or await get_github_app_installation_token()
        if not token:
            return
        repo, issue_number = github_target
        await post_github_comment(repo, issue_number, _HARD_STOP_MESSAGE, token=token)
        logger.info("Sent write_todos loop closeout to GitHub item #%s", issue_number)
        return

    logger.info("No user-facing target found for write_todos loop closeout")


class ConsecutiveToolGuardMiddleware(AgentMiddleware[AgentState, Any]):
    """Nudge, then stop, runs stuck re-planning with write_todos and no progress."""

    state_schema = AgentState

    def __init__(
        self,
        *,
        soft_threshold: int | None = None,
        hard_ceiling: int | None = None,
    ) -> None:
        self.soft_threshold = soft_threshold or _env_int(
            "OPEN_SWE_WRITE_TODOS_SOFT_THRESHOLD", DEFAULT_SOFT_THRESHOLD
        )
        self.hard_ceiling = hard_ceiling or _env_int(
            "OPEN_SWE_WRITE_TODOS_HARD_CEILING", DEFAULT_HARD_CEILING
        )

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        messages = state.get("messages", [])
        count = consecutive_write_todos_count(messages)

        if count >= self.hard_ceiling:
            content = (
                f"{_GUARD_MARKER}: {count} consecutive write_todos calls. {_HARD_STOP_MESSAGE}"
            )
            return {"jump_to": "end", "messages": [AIMessage(content=content)]}

        if count >= self.soft_threshold:
            last_text = _content_to_text(getattr(messages[-1], "content", "") or "")
            if _NUDGE_MARKER in last_text:
                return None
            instruction = _NUDGE_INSTRUCTION.format(count=count)
            return {"messages": [SystemMessage(content=f"{_NUDGE_MARKER}\n{instruction}")]}

        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    async def aafter_agent(
        self,
        state: AgentState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None
        content = _content_to_text(getattr(messages[-1], "content", "") or "")
        if _GUARD_MARKER not in content:
            return None
        try:
            config = get_config()
            await _post_closeout(config)
        except Exception:
            logger.exception("Failed to send write_todos loop closeout")
        return None
