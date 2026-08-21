"""One way to reach the human who triggered a run.

Every run has an originating channel — a Slack thread, a Linear issue, or a
GitHub issue/PR — but where that channel is recorded depends on who is asking:
thread metadata (the run-completion webhook), the run's ``configurable``
(in-graph middleware), or a stored baby-sit watch. The three builders here
parse those shapes into one :class:`SourceContext`, and
:func:`notify_source_channel` walks the Slack → Linear → GitHub ladder over it
exactly once.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypedDict

from langgraph_sdk.client import LangGraphClient

from ..config import langgraph_client
from ..github.app import get_github_app_installation_token
from ..github.comments import post_github_comment
from ..github.token import get_github_token
from .linear import comment_on_linear_issue
from .slack_api import post_slack_thread_reply
from .slack_threads import get_active_slack_thread
from .user_messages import warning

logger = logging.getLogger(__name__)


class SlackThreadRef(TypedDict):
    channel_id: str
    thread_ts: str


class GitHubItemRef(TypedDict):
    repo: dict[str, str]
    number: int


class SourceContext(TypedDict):
    """Where a run's messages go back to, with every shape already resolved."""

    source: str | None
    slack_thread: SlackThreadRef | None
    linear_issue_id: str | None
    github_item: GitHubItemRef | None


GitHubTokenResolver = Callable[[], Awaitable[str | None]]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _slack_thread_ref(value: object) -> SlackThreadRef | None:
    raw = _mapping(value)
    if raw is None:
        return None
    channel_id = _text(raw.get("channel_id"))
    thread_ts = _text(raw.get("thread_ts"))
    if channel_id is None or thread_ts is None:
        return None
    return {"channel_id": channel_id, "thread_ts": thread_ts}


def _linear_issue_id(value: object) -> str | None:
    raw = _mapping(value)
    return _text(raw.get("id")) if raw is not None else None


def _repo_ref(value: object) -> dict[str, str] | None:
    raw = _mapping(value)
    if raw is None:
        return None
    owner = _text(raw.get("owner"))
    name = _text(raw.get("name"))
    if owner is None or name is None:
        return None
    return {"owner": owner, "name": name}


def _github_item(repo: dict[str, str] | None, number: int | None) -> GitHubItemRef | None:
    if repo is None or number is None:
        return None
    return {"repo": repo, "number": number}


def source_context_from_thread_metadata(metadata: Mapping[str, Any]) -> SourceContext:
    """Parse an Open SWE thread's persisted metadata.

    The repo lives at the top level of thread metadata while the item number
    lives in ``source_context``, so both halves are read here.
    """
    context = _mapping(metadata.get("source_context")) or {}
    number = _number(context.get("pr_number"))
    if number is None:
        github_issue = _mapping(context.get("github_issue"))
        number = _number(github_issue.get("number")) if github_issue is not None else None
    return {
        "source": _text(metadata.get("source")),
        "slack_thread": _slack_thread_ref(context.get("slack_thread")),
        "linear_issue_id": _linear_issue_id(context.get("linear_issue")),
        "github_item": _github_item(_repo_ref(metadata.get("repo")), number),
    }


def source_context_from_configurable(configurable: Mapping[str, Any]) -> SourceContext:
    """Parse a live run's ``configurable``.

    ``github_pr_or_issue`` may name a repo other than the run's own — a PR
    comment can ask for work on a fork — so its repo wins when it carries one.
    """
    repo = _repo_ref(configurable.get("repo"))
    number: int | None = None

    pr_or_issue = _mapping(configurable.get("github_pr_or_issue"))
    if pr_or_issue is not None:
        number = _number(pr_or_issue.get("number"))
        repo = _repo_ref(pr_or_issue.get("repo")) or repo

    if number is None:
        github_issue = _mapping(configurable.get("github_issue"))
        number = _number(github_issue.get("number")) if github_issue is not None else None
    if number is None:
        number = _number(configurable.get("pr_number"))

    return {
        "source": _text(configurable.get("source")),
        "slack_thread": _slack_thread_ref(configurable.get("slack_thread")),
        "linear_issue_id": _linear_issue_id(configurable.get("linear_issue")),
        "github_item": _github_item(repo, number),
    }


def source_context_from_watch(watch: Mapping[str, Any]) -> SourceContext:
    """Parse a stored baby-sit watch.

    A watch always knows its own repo and PR, so the GitHub rung is reachable
    even when the watch was started from a channel that left no context.
    """
    context = _mapping(watch.get("source_context")) or {}
    run_config = _mapping(watch.get("run_config")) or {}
    github_issue = _mapping(context.get("github_issue"))
    number = _number(github_issue.get("number")) if github_issue is not None else None
    if number is None:
        number = _number(run_config.get("pr_number"))
    return {
        "source": _text(run_config.get("source")),
        "slack_thread": _slack_thread_ref(context.get("slack_thread")),
        "linear_issue_id": _linear_issue_id(context.get("linear_issue")),
        "github_item": _github_item(
            _repo_ref({"owner": watch.get("owner"), "name": watch.get("repo")}), number
        ),
    }


def in_graph_github_token(config: Mapping[str, Any]) -> GitHubTokenResolver:
    """Token strategy for code running inside a graph.

    In-graph callers can read the triggering user's token out of the per-thread
    cache, which keeps the comment attributed to them and scoped to the repos
    they can already reach; the app installation is only the fallback. Callers
    outside the graph runtime have no such cache, so they pass
    :func:`~agent.github.app.get_github_app_installation_token` directly.
    """

    async def resolve() -> str | None:
        return get_github_token(config) or await get_github_app_installation_token()

    return resolve


async def _slack_target(
    context: SourceContext,
    agent_thread_id: str | None,
    client_factory: Callable[[], LangGraphClient] | None,
) -> SlackThreadRef | None:
    fallback = context["slack_thread"]
    if client_factory is None:
        return fallback
    active = await get_active_slack_thread(client_factory(), agent_thread_id, fallback)
    return _slack_thread_ref(active)


async def notify_source_channel(
    context: SourceContext,
    text: str,
    *,
    github_token: GitHubTokenResolver,
    agent_thread_id: str | None = None,
    slack_text: str | None = None,
    langgraph_client_factory: Callable[[], LangGraphClient] | None = None,
) -> bool:
    """Post ``text`` to the run's originating channel. Best-effort; never raises.

    ``slack_text`` overrides ``text`` on the Slack rung only, for copy that uses
    Slack link markup other channels would render as literal characters.

    ``langgraph_client_factory``, when given, re-reads the Slack location from
    the live thread so a Slack thread that moved since the run started still
    gets the message; pass the factory the calling process is allowed to use.
    Callers holding freshly-read thread metadata leave it unset rather than pay
    for a second round trip to learn what they already know.
    """
    try:
        slack = await _slack_target(context, agent_thread_id, langgraph_client_factory)
        if slack is not None:
            body = slack_text if slack_text else text
            if agent_thread_id:
                posted = await post_slack_thread_reply(
                    slack["channel_id"], slack["thread_ts"], body, agent_thread_id=agent_thread_id
                )
            else:
                posted = await post_slack_thread_reply(
                    slack["channel_id"], slack["thread_ts"], body
                )
            logger.info("Notified Slack thread %s", slack["thread_ts"])
            return posted

        linear_issue_id = context["linear_issue_id"]
        if linear_issue_id is not None:
            posted = await comment_on_linear_issue(linear_issue_id, text)
            logger.info("Notified Linear issue %s", linear_issue_id)
            return posted

        github_item = context["github_item"]
        if github_item is not None:
            token = await github_token()
            if not token:
                logger.info("No GitHub token available to notify #%s", github_item["number"])
                return False
            posted = await post_github_comment(
                github_item["repo"], github_item["number"], text, token=token
            )
            logger.info("Notified GitHub item #%s", github_item["number"])
            return posted
    except Exception:
        logger.warning("Failed to notify the source channel", exc_info=True)
        return False

    logger.info("No source channel to notify (source=%s)", context["source"])
    return False


def sandbox_unreachable_message(
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    replacement_attempted: bool = False,
) -> str:
    """User-facing text for a sandbox that stopped answering.

    Deliberately does not claim the sandbox is gone for good — all we observed is
    that it stopped responding, and it may come back. ``replacement_attempted``
    is for callers allowed to replace an unreachable sandbox (the read-only
    reviewer), where "Open SWE will not start a replacement" would be untrue.
    """
    identifiers = [
        part
        for part in (
            f"name {sandbox_name}" if sandbox_name else None,
            f"id {sandbox_id}" if sandbox_id else None,
        )
        if part
    ]
    which = f" ({', '.join(identifiers)})" if identifiers else ""
    if replacement_attempted:
        return warning(
            f"This thread's sandbox{which} stopped responding and Open SWE could "
            "not provision a replacement, so this run had nowhere to work. "
            "Retrigger this thread to try again."
        )
    return warning(
        f"This thread's sandbox{which} stopped responding, and Open SWE can't tell "
        "whether it will come back. Open SWE will not start a replacement on its "
        "own: a new sandbox is empty, so swapping one in would throw away anything "
        "not yet committed and pushed while still looking like a recovery. "
        "Retrigger this thread to try the same sandbox again, or start a new thread "
        "to get a fresh one."
    )


_SANDBOX_ID_RE = re.compile(r"\bsb-[A-Za-z0-9-]+\b")


def extract_sandbox_id(text: str) -> str | None:
    match = _SANDBOX_ID_RE.search(text)
    return match.group(0) if match else None


async def post_sandbox_unreachable_notification(
    config: Mapping[str, Any],
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    replacement_attempted: bool = False,
) -> None:
    """Tell the user, on the channel they triggered from, that their sandbox went quiet."""
    configurable = _mapping(config.get("configurable"))
    if configurable is None:
        logger.info("No runtime configurable found for sandbox unreachable notification")
        return

    await notify_source_channel(
        source_context_from_configurable(configurable),
        sandbox_unreachable_message(
            sandbox_id=sandbox_id,
            sandbox_name=sandbox_name,
            replacement_attempted=replacement_attempted,
        ),
        github_token=in_graph_github_token(config),
        agent_thread_id=_text(configurable.get("thread_id")),
        langgraph_client_factory=langgraph_client,
    )


__all__ = [
    "GitHubItemRef",
    "GitHubTokenResolver",
    "SlackThreadRef",
    "SourceContext",
    "extract_sandbox_id",
    "in_graph_github_token",
    "notify_source_channel",
    "post_sandbox_unreachable_notification",
    "sandbox_unreachable_message",
    "source_context_from_configurable",
    "source_context_from_thread_metadata",
    "source_context_from_watch",
]
