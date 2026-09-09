"""Linear agent-session lifecycle, plus the pieces the comment trigger shares with it.

Linear expects an activity within ten seconds of an ``AgentSessionEvent``, so both
entry points emit a thought before doing anything that can block.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any, cast

import httpx2
from langchain_core.messages.content import create_text_block

from agent.dashboard.agent_overrides import (
    get_profile_default_repo,
    resolve_agent_model_id,
    resolve_login_from_email_async,
)
from agent.dashboard.options import default_vision_model_pair, model_supports_images
from agent.dashboard.team_settings import get_team_default_repo
from agent.dispatch import dispatch_agent_run
from agent.input_messages import (
    PersonIdentity,
    RunInput,
    RunMessage,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from agent.linear.activities import stream_linear_activities
from agent.linear.client import LinearError, linear_client
from agent.linear.comments import get_recent_comments, is_agent_message
from agent.linear.schema import (
    ActionContent,
    AgentActivity,
    AgentActivityContent,
    AgentSessionEvent,
    ErrorContent,
    GuidanceRule,
    LinearComment,
    LinearIssue,
    LinearUser,
    ThoughtContent,
)
from agent.source_context import SourceContext
from agent.thread_ids import linear_issue_thread_id
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.http import DEFAULT_HTTP_TIMEOUT
from agent.utils.multimodal import dedupe_urls, extract_image_urls, fetch_image_block
from agent.utils.repo import extract_repo_from_text
from agent.utils.thread_ops import (
    get_thread_active_status,
    langgraph_client,
    queue_message_for_thread,
)
from agent.webhooks.common import (
    AGENT_VERSION_METADATA,
    DEFAULT_REPO_OWNER,
    extract_repo_config_from_thread,
    get_repo_config_from_team_mapping,
    is_repo_allowed,
    upsert_agent_thread_metadata,
)

logger = logging.getLogger(__name__)

_SYSTEM_SENDER_ID = "system:linear-issue"

_PR_CONVENTIONS = (
    "Please analyze this issue and implement the necessary changes. "
    "If you open a PR for this issue, make sure the PR description links back to "
    "this Linear ticket and follows this repository's PR conventions for the title, body, "
    "release note, and/or changelog. Inspect AGENTS.md, PR templates, "
    ".changelog/README.md, and nearby docs before choosing the PR title/body format. "
    "When you're done, commit and push your changes."
)

_PICKUP_THOUGHT = "Picking this up — resolving the repository and starting a workspace."
_FOLLOW_UP_THOUGHT = "Got it — picking that up now."
_QUEUED_THOUGHT = "Got it — I'll pick that up after the current step."

_NO_ISSUE_ERROR = (
    "This session isn't attached to an issue, so there is nothing for me to work on. "
    "Mention me on an issue instead."
)
_NO_REPO_ERROR = (
    "I don't know which repository to work in. Name one in your message as `owner/name`, "
    "or set a default repository in Open SWE Web."
)

# Linear guidance is free prose, so only an unambiguous slug counts as a repository:
# one written as ``repo: owner/name``, as a GitHub URL, or inside backticks.
_BACKTICKED_REPO_RE = re.compile(r"`([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)`")


async def emit_activity(
    session_id: str, content: AgentActivityContent, *, ephemeral: bool = False
) -> bool:
    """Post one agent activity; a Linear failure must never abort the run behind it."""
    try:
        await linear_client().create_agent_activity(session_id, content, ephemeral=ephemeral)
    except LinearError, httpx2.HTTPError:
        logger.warning(
            "Linear agent activity failed",
            extra={"linear_session_id": session_id, "linear_activity_type": content.type},
            exc_info=True,
        )
        return False
    return True


async def is_own_comment(comment: LinearComment) -> bool:
    """Whether this app authored the comment, so a reply of ours never triggers a run."""
    try:
        viewer_id = await linear_client().viewer_id()
    except LinearError, httpx2.HTTPError:
        logger.warning("Linear viewer lookup failed", exc_info=True)
        return is_agent_message(comment.body)
    return comment.user is not None and comment.user.id == viewer_id


async def load_issue(issue: LinearIssue) -> LinearIssue:
    """Full issue details; webhook payloads omit the project the team map keys on."""
    try:
        full_issue = await linear_client().get_issue(issue.id)
    except LinearError, httpx2.HTTPError:
        logger.warning("Linear issue fetch failed", extra={"linear_issue_id": issue.id})
        return issue
    return full_issue or issue


def _repo_from_guidance(body: str) -> dict[str, str] | None:
    named = extract_repo_from_text(body, default_owner=DEFAULT_REPO_OWNER)
    if named:
        return named
    match = _BACKTICKED_REPO_RE.search(body)
    return {"owner": match.group(1), "name": match.group(2)} if match else None


async def resolve_repo_config(
    *,
    trigger_text: str,
    requester_email: str,
    issue: LinearIssue | None,
    guidance: Sequence[GuidanceRule] = (),
) -> dict[str, str] | None:
    """The repository a Linear trigger should run against.

    Precedence: named in the triggering text, the requester's dashboard default,
    workspace guidance, the team/project map, then the team-wide default.
    """
    named = extract_repo_from_text(trigger_text, default_owner=DEFAULT_REPO_OWNER)
    if named:
        return named

    if requester_email:
        try:
            profile_repo = await get_profile_default_repo(
                await resolve_login_from_email_async(requester_email)
            )
        except Exception:  # noqa: BLE001
            logger.warning("Linear dashboard default-repo lookup failed", exc_info=True)
            profile_repo = None
        if profile_repo:
            return profile_repo

    for rule in guidance:
        from_guidance = _repo_from_guidance(rule.body)
        if from_guidance:
            return from_guidance

    team = issue.team if issue else None
    project = issue.project if issue else None
    from_mapping = get_repo_config_from_team_mapping(
        (team.name or "").strip() if team else "",
        (project.name or "").strip() if project else "",
    )
    if from_mapping:
        return from_mapping

    return await get_team_default_repo()


async def thread_repo_config(thread_id: str) -> dict[str, str] | None:
    """The repository an existing Linear thread already runs against."""
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not read Linear thread metadata", extra={"linear_thread_id": thread_id})
        return None
    return extract_repo_config_from_thread(thread)


def first_identified(*candidates: LinearUser | None) -> LinearUser | None:
    for candidate in candidates:
        if candidate is not None and (candidate.email or candidate.name):
            return candidate
    return None


def activity_body(activity: AgentActivity | None) -> str:
    """The prose an agent activity carries; action activities have none."""
    if activity is None:
        return ""
    content = activity.content
    return "" if isinstance(content, ActionContent) else content.body


def replay_comments(
    issue: LinearIssue,
    *,
    trigger: LinearComment | None = None,
    previous: Sequence[LinearComment] = (),
) -> list[LinearComment]:
    """The comments to replay as human turns, oldest first."""
    trigger_index = (
        next((i for i, item in enumerate(issue.comments) if item.id == trigger.id), None)
        if trigger is not None
        else None
    )
    if previous:
        candidates: list[LinearComment] = list(previous)
    elif trigger_index is not None:
        candidates = list(issue.comments[trigger_index:])
    else:
        candidates = get_recent_comments(issue.comments)

    replayed = [comment for comment in candidates if not is_agent_message(comment.body)]
    if trigger is not None and trigger.body and all(item.id != trigger.id for item in replayed):
        replayed.append(trigger)
    return replayed


def _tag_instruction(user_name: str) -> str:
    if not user_name:
        return ""
    return (
        f"When calling linear_comment, tag @{user_name} if you are asking them a question, "
        "need their input, or are notifying them of something important (e.g. a completed PR). "
        "For simple answers, tagging is not required."
    )


def system_text(
    prompt_context: str | None,
    issue: LinearIssue,
    repo_config: dict[str, str],
    *,
    user_name: str,
) -> str:
    """The system turn that opens a Linear run, from Linear's own context when it sent one."""
    conventions = f"{_PR_CONVENTIONS} {_tag_instruction(user_name)}".strip()
    if prompt_context:
        return f"{prompt_context}\n\n{conventions}"

    triggered_by_line = f"## Triggered by: {user_name}\n\n" if user_name else ""
    ticket_url_line = f"## Linear Ticket URL: {issue.url}\n\n" if issue.url else ""
    return (
        "Please work on the following issue:\n\n"
        f"## Repository: {repo_config['owner']}/{repo_config['name']}\n\n"
        f"## Title: {issue.title or 'No title'}\n\n"
        f"{triggered_by_line}"
        f"## Linear Ticket: {issue.identifier or ''} - Ticket ID: {issue.id}\n\n"
        f"{ticket_url_line}"
        f"## Description:\n{issue.description or 'No description'}\n\n"
        f"{conventions}"
    )


def linear_issue_config(
    issue: LinearIssue, *, user_name: str, agent_session_id: str | None = None
) -> dict[str, Any]:
    """The ``linear_issue`` block carried in the run config and the thread's source context."""
    identifier = issue.identifier or ""
    project_id, _, issue_number = identifier.partition("-")
    config: dict[str, Any] = {
        "id": issue.id,
        "title": issue.title or "",
        "url": issue.url or "",
        "identifier": identifier,
        "linear_project_id": project_id if issue_number else "",
        "linear_issue_number": issue_number,
        "triggering_user_name": user_name,
    }
    if agent_session_id:
        config["agent_session_id"] = agent_session_id
    return config


def run_configurable(
    *,
    repo_config: dict[str, str],
    linear_issue: dict[str, Any],
    user_email: str,
    github_login: str | None,
    model_override: tuple[str, str] | None,
) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "repo": repo_config,
        "linear_issue": linear_issue,
        "user_email": user_email or None,
        "source": "linear",
    }
    if github_login:
        configurable["github_login"] = github_login
    if model_override:
        configurable["agent_model_id"] = model_override[0]
        configurable["agent_effort"] = model_override[1]
    return configurable


async def upsert_thread_metadata(
    thread_id: str,
    *,
    issue: LinearIssue,
    repo_config: dict[str, str],
    linear_issue: dict[str, Any],
    github_login: str | None,
    user_email: str,
) -> None:
    await upsert_agent_thread_metadata(
        thread_id,
        source="linear",
        repo_config=repo_config,
        github_login=github_login or "",
        user_email=user_email,
        title=issue.title or issue.identifier or "Linear issue",
        source_context=SourceContext.parse({"linear_issue": linear_issue}),
    )


def _comment_person(comment: LinearComment) -> tuple[PersonIdentity, str]:
    author = comment.user
    key = (author.id or author.email or author.name) if author is not None else None
    sender_id = f"linear:{(key or 'unknown').replace(' ', '-')}"
    person: PersonIdentity = {"id": sender_id, "platform": "linear"}
    if author is not None and author.name:
        person["display_name"] = author.name
    if author is not None and author.email:
        person["email"] = author.email
    return person, sender_id


async def _fetch_image_blocks(urls: Sequence[str]) -> dict[str, dict[str, Any]]:
    blocks: dict[str, dict[str, Any]] = {}
    async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        for url in urls:
            block = await fetch_image_block(url, client)
            if block:
                blocks[url] = cast(dict[str, Any], block)
    return blocks


async def _vision_override(image_count: int, github_login: str | None) -> tuple[str, str] | None:
    resolved_model_id = await resolve_agent_model_id(github_login)
    if model_supports_images(resolved_model_id):
        return None
    fallback_model_id, fallback_effort = default_vision_model_pair()
    logger.info(
        "Falling back to a vision model for Linear images",
        extra={
            "linear_image_count": image_count,
            "linear_configured_model": resolved_model_id,
            "linear_fallback_model": fallback_model_id,
        },
    )
    return fallback_model_id, fallback_effort


async def build_run_input(
    *,
    issue: LinearIssue,
    repo_config: dict[str, str],
    text: str | None,
    comments: Sequence[LinearComment],
    github_login: str | None,
) -> tuple[RunInput, tuple[str, str] | None]:
    """Assemble a Linear run's messages, keeping each image with the turn that carried it.

    ``text`` opens the run with a system turn; a follow-up on an existing thread
    passes ``None`` and contributes only the human turns.
    """
    description_image_urls = (
        dedupe_urls(extract_image_urls(issue.description or "")) if text is not None else []
    )
    image_urls_by_comment_id: dict[str, list[str]] = {}
    image_urls = list(description_image_urls)
    for comment in comments:
        urls = dedupe_urls(extract_image_urls(comment.body))
        if urls:
            image_urls_by_comment_id[comment.id] = urls
            image_urls.extend(urls)
    image_urls = dedupe_urls(image_urls)

    model_override: tuple[str, str] | None = None
    image_blocks_by_url: dict[str, dict[str, Any]] = {}
    if image_urls:
        model_override = await _vision_override(len(image_urls), github_login)
        image_blocks_by_url = await _fetch_image_blocks(image_urls)

    messages: list[RunMessage] = []
    if text is not None:
        system_blocks: list[dict[str, Any]] = [cast(dict[str, Any], create_text_block(text))]
        system_blocks.extend(
            image_blocks_by_url[url] for url in description_image_urls if url in image_blocks_by_url
        )
        messages.append(
            system_introduction(
                {"id": _SYSTEM_SENDER_ID, "display_name": "Linear issue", "platform": "linear"}
            )
        )
        messages.append(
            system_input(
                system_blocks if len(system_blocks) > 1 else text,
                {
                    "sender_id": _SYSTEM_SENDER_ID,
                    "surface": "linear",
                    "kind": "system",
                    "data": {
                        "issue": {
                            "id": issue.id,
                            "identifier": issue.identifier or "",
                            "url": issue.url or "",
                            "repository": f"{repo_config['owner']}/{repo_config['name']}",
                            "title": issue.title or "",
                        }
                    },
                },
            )
        )

    introduced: set[str] = set()
    for comment in comments:
        person, sender_id = _comment_person(comment)
        if sender_id not in introduced:
            messages.append(person_introduction(person))
            introduced.add(sender_id)
        comment_blocks = [
            image_blocks_by_url[url]
            for url in image_urls_by_comment_id.get(comment.id, [])
            if url in image_blocks_by_url
        ]
        content: str | list[dict[str, Any]] = comment.body
        if comment_blocks:
            content = [cast(dict[str, Any], create_text_block(comment.body)), *comment_blocks]
        messages.append(
            human_input(
                content,
                {
                    "sender_id": sender_id,
                    "surface": "linear",
                    "kind": "human",
                    "data": {"comment_id": comment.id},
                },
            )
        )
    return {"messages": messages}, model_override


async def dispatch_linear_run(
    thread_id: str,
    configurable: dict[str, Any],
    run_input: RunInput,
) -> str | None:
    run = await dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source="linear",
        input=run_input,
        metadata=AGENT_VERSION_METADATA,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else None
    logger.info(
        "Linear run dispatched",
        extra={"linear_thread_id": thread_id, "linear_run_id": run_id},
    )
    return run_id if isinstance(run_id, str) and run_id else None


async def _link_session_to_dashboard(session_id: str, thread_id: str) -> None:
    url = dashboard_thread_url(thread_id)
    if not url:
        return
    try:
        await linear_client().update_agent_session_external_url(session_id, url)
    except LinearError, httpx2.HTTPError:
        logger.warning(
            "Linear external-link update failed",
            extra={"linear_session_id": session_id},
            exc_info=True,
        )


async def start_session(event: AgentSessionEvent) -> None:
    """Pick up an issue Linear delegated to us, and mirror the run into the session."""
    session = event.agent_session
    await emit_activity(session.id, ThoughtContent(body=_PICKUP_THOUGHT))

    if session.issue is None:
        logger.warning("Linear agent session has no issue", extra={"linear_session_id": session.id})
        await emit_activity(session.id, ErrorContent(body=_NO_ISSUE_ERROR))
        return

    issue = await load_issue(session.issue)
    trigger = session.source_comment or session.comment
    requester = first_identified(
        session.creator,
        trigger.user if trigger is not None else None,
        issue.creator,
        issue.assignee,
    )
    requester_email = (requester.email or "") if requester is not None else ""
    requester_name = (requester.name or requester.display_name or "") if requester else ""
    trigger_text = "\n".join(
        part
        for part in (
            (trigger.body if trigger is not None else ""),
            activity_body(event.agent_activity),
        )
        if part
    )

    repo_config = await resolve_repo_config(
        trigger_text=trigger_text,
        requester_email=requester_email,
        issue=issue,
        guidance=event.guidance,
    )
    if not repo_config:
        await emit_activity(session.id, ErrorContent(body=_NO_REPO_ERROR))
        return
    if not is_repo_allowed(repo_config):
        logger.warning(
            "Rejecting a Linear agent session for a repository outside the allowlist",
            extra={"linear_repo": f"{repo_config['owner']}/{repo_config['name']}"},
        )
        await emit_activity(
            session.id,
            ErrorContent(
                body=f"`{repo_config['owner']}/{repo_config['name']}` is not in this "
                "deployment's repository allowlist, so I can't work on it."
            ),
        )
        return

    github_login = (
        await resolve_login_from_email_async(requester_email) if requester_email else None
    )
    comments = replay_comments(issue, trigger=trigger, previous=event.previous_comments)
    run_input, model_override = await build_run_input(
        issue=issue,
        repo_config=repo_config,
        text=system_text(event.prompt_context, issue, repo_config, user_name=requester_name),
        comments=comments,
        github_login=github_login,
    )

    thread_id = linear_issue_thread_id(issue.id)
    linear_issue = linear_issue_config(issue, user_name=requester_name, agent_session_id=session.id)
    await upsert_thread_metadata(
        thread_id,
        issue=issue,
        repo_config=repo_config,
        linear_issue=linear_issue,
        github_login=github_login,
        user_email=requester_email,
    )
    run_id = await dispatch_linear_run(
        thread_id,
        run_configurable(
            repo_config=repo_config,
            linear_issue=linear_issue,
            user_email=requester_email,
            github_login=github_login,
            model_override=model_override,
        ),
        run_input,
    )
    await _link_session_to_dashboard(session.id, thread_id)
    if run_id:
        await stream_linear_activities(
            client=langgraph_client(),
            thread_id=thread_id,
            run_id=run_id,
            session_id=session.id,
        )


async def continue_session(event: AgentSessionEvent) -> None:
    """Feed a follow-up prompt into the run or the thread the session already owns."""
    session = event.agent_session
    body = activity_body(event.agent_activity)
    if session.issue is None or not body:
        logger.info(
            "Ignoring a Linear prompt with no issue or body",
            extra={"linear_session_id": session.id},
        )
        return

    thread_id = linear_issue_thread_id(session.issue.id)
    if await get_thread_active_status(thread_id):
        await queue_message_for_thread(thread_id, body)
        await emit_activity(session.id, ThoughtContent(body=_QUEUED_THOUGHT), ephemeral=True)
        return

    await emit_activity(session.id, ThoughtContent(body=_FOLLOW_UP_THOUGHT))

    activity = event.agent_activity
    issue = await load_issue(session.issue)
    requester = first_identified(
        activity.user if activity is not None else None, session.creator, issue.creator
    )
    requester_email = (requester.email or "") if requester is not None else ""
    requester_name = (requester.name or requester.display_name or "") if requester else ""

    repo_config = await thread_repo_config(thread_id) or await resolve_repo_config(
        trigger_text=body,
        requester_email=requester_email,
        issue=issue,
        guidance=event.guidance,
    )
    if not repo_config or not is_repo_allowed(repo_config):
        await emit_activity(session.id, ErrorContent(body=_NO_REPO_ERROR))
        return

    github_login = (
        await resolve_login_from_email_async(requester_email) if requester_email else None
    )
    follow_up = LinearComment(
        id=(activity.id if activity is not None and activity.id else session.id),
        body=body,
        user=requester,
    )
    run_input, model_override = await build_run_input(
        issue=issue,
        repo_config=repo_config,
        text=None,
        comments=[follow_up],
        github_login=github_login,
    )

    linear_issue = linear_issue_config(issue, user_name=requester_name, agent_session_id=session.id)
    await upsert_thread_metadata(
        thread_id,
        issue=issue,
        repo_config=repo_config,
        linear_issue=linear_issue,
        github_login=github_login,
        user_email=requester_email,
    )
    run_id = await dispatch_linear_run(
        thread_id,
        run_configurable(
            repo_config=repo_config,
            linear_issue=linear_issue,
            user_email=requester_email,
            github_login=github_login,
            model_override=model_override,
        ),
        run_input,
    )
    if run_id:
        await stream_linear_activities(
            client=langgraph_client(),
            thread_id=thread_id,
            run_id=run_id,
            session_id=session.id,
        )
