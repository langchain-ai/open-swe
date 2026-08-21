"""Linear webhook handling: repo resolution and the issue-triggered run."""

import logging
from collections.abc import Sequence
from typing import Any, cast

import httpx
from langchain_core.messages.content import create_text_block

from ..config import (
    agent_version_metadata,
    default_repo_name,
    default_repo_owner,
)
from ..dispatch import dispatch_agent_run
from ..input_messages import (
    PersonIdentity,
    RunInput,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from ..settings.agent_overrides import resolve_agent_model_id, resolve_login_from_email_async
from ..settings.options import default_vision_model_pair, model_supports_images
from ..settings.team_settings import get_team_default_repo
from ..thread_ids import linear_issue_thread_id
from ..utils.http import DEFAULT_HTTP_TIMEOUT
from ..utils.linear import fetch_issue_details, post_linear_trace_comment, react_to_linear_comment
from ..utils.linear_team_repo_map import LINEAR_TEAM_TO_REPO
from ..utils.multimodal import dedupe_urls, extract_image_urls, fetch_image_block
from ..utils.repo import extract_repo_from_text
from ..utils.thread_ops import upsert_agent_thread_owner_metadata
from .repo_config import profile_default_repo_for_email

logger = logging.getLogger(__name__)

BOT_MESSAGE_PREFIXES = (
    "🔐 **GitHub Authentication Required**",
    "✅ **Pull Request Created**",
    "✅ **Pull Request Updated**",
    "**Pull Request Created**",
    "**Pull Request Updated**",
    "🤖 **Agent Response**",
    "❌ **Agent Error**",
)


def get_repo_config_from_team_mapping(
    team_identifier: str, project_name: str = ""
) -> dict[str, str]:
    """Look up repository configuration from the ``LINEAR_TEAM_TO_REPO`` mapping."""
    default_name = default_repo_name()
    fallback = {"owner": default_repo_owner(), "name": default_name} if default_name else {}

    if not team_identifier or team_identifier not in LINEAR_TEAM_TO_REPO:
        return fallback

    config = LINEAR_TEAM_TO_REPO[team_identifier]

    if "owner" in config and "name" in config:
        return config

    projects = config.get("projects")
    if isinstance(projects, dict) and project_name:
        project_config = projects.get(project_name)
        if isinstance(project_config, dict):
            return project_config

    default = config.get("default")
    if isinstance(default, dict):
        return default

    return fallback


async def get_linear_repo_config(
    comment_body: str, *, comment_user_email: str | None, issue: dict[str, Any]
) -> dict[str, str] | None:
    """Resolve the repository a Linear comment trigger operates on.

    Priority:
        1. A ``repo:owner/name`` token in the comment body.
        2. The commenting user's dashboard ``default_repo``.
        3. The ``LINEAR_TEAM_TO_REPO`` team/project mapping.
        4. Team default repo.
    """
    repo_config = extract_repo_from_text(comment_body, default_owner=default_repo_owner())
    if repo_config:
        logger.debug(
            "Using repo from comment body: %s/%s", repo_config["owner"], repo_config["name"]
        )
        return repo_config

    profile_repo = await profile_default_repo_for_email(comment_user_email, channel="Linear")
    if profile_repo:
        return profile_repo

    team = issue.get("team") or {}
    project = issue.get("project") or {}
    team_identifier = str(team.get("name") or "").strip()
    project_key = str(project.get("name") or "").strip()
    repo_config = get_repo_config_from_team_mapping(team_identifier, project_key)
    logger.debug(
        "Team/project lookup result",
        extra={
            "team_name": team_identifier,
            "project_name": project_key,
            "repo_config": repo_config,
        },
    )
    if repo_config:
        return repo_config

    return await get_team_default_repo()


def _recent_comments(comments: Sequence[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Comments posted since the last agent response, oldest first, or None if there are none."""
    if not comments:
        return None

    sorted_comments = sorted(
        comments,
        key=lambda comment: comment.get("createdAt", ""),
        reverse=True,
    )

    recent_user_comments: list[dict[str, Any]] = []
    for comment in sorted_comments:
        body = comment.get("body", "")
        if any(body.startswith(prefix) for prefix in BOT_MESSAGE_PREFIXES):
            break  # Everything after this is from before the last agent response
        recent_user_comments.append(comment)

    if not recent_user_comments:
        return None

    recent_user_comments.reverse()
    return recent_user_comments


async def process_linear_issue(  # noqa: PLR0912, PLR0915
    issue_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    """Process a Linear issue by creating a new LangGraph thread and run.

    Args:
        issue_data: The Linear issue data from webhook (basic info only).
        repo_config: The repo configuration with owner and name.
    """
    issue_id = issue_data.get("id", "")
    logger.info(
        "Processing Linear issue %s for repo %s/%s",
        issue_id,
        repo_config.get("owner"),
        repo_config.get("name"),
    )

    triggering_comment_id = issue_data.get("triggering_comment_id", "")
    if triggering_comment_id:
        await react_to_linear_comment(triggering_comment_id, "👀")

    thread_id = linear_issue_thread_id(issue_id)

    full_issue = await fetch_issue_details(issue_id)
    if not full_issue:
        full_issue = issue_data

    user_email = None
    user_name = None
    comment_author = issue_data.get("comment_author", {})
    if comment_author:
        user_email = comment_author.get("email")
        user_name = comment_author.get("name")
    if not user_email:
        creator = full_issue.get("creator", {})
        if creator:
            user_email = creator.get("email")
            user_name = user_name or creator.get("name")
    if not user_email:
        assignee = full_issue.get("assignee", {})
        if assignee:
            user_email = assignee.get("email")
            user_name = user_name or assignee.get("name")

    logger.info("User email for issue %s: %s", issue_id, user_email)

    title = full_issue.get("title", "No title")
    description = full_issue.get("description") or "No description"
    image_urls: list[str] = []
    description_image_urls = extract_image_urls(description)
    if description_image_urls:
        image_urls.extend(description_image_urls)
        logger.debug(
            "Found %d image URL(s) in issue description",
            len(description_image_urls),
        )

    comments = full_issue.get("comments", {}).get("nodes", [])
    included_comments: list[dict[str, Any]] = []
    image_urls_by_comment_id: dict[str, list[str]] = {}
    triggering_comment = issue_data.get("triggering_comment", "")
    triggering_comment_id = issue_data.get("triggering_comment_id", "")

    comment_ids: set[str] = set()
    comment_id_to_index: dict[str, int] = {}
    if comments:
        for i, comment in enumerate(comments):
            comment_id = comment.get("id", "")
            if comment_id:
                comment_ids.add(comment_id)
                comment_id_to_index[comment_id] = i

        relevant_comments = []
        trigger_index = None
        if triggering_comment_id:
            trigger_index = comment_id_to_index.get(triggering_comment_id)
        if trigger_index is not None:
            relevant_comments = comments[trigger_index:]
            logger.debug(
                "Using triggering comment index %d to build relevant comments",
                trigger_index,
            )
        else:
            relevant_comments = _recent_comments(comments)

        if relevant_comments:
            for comment in relevant_comments:
                user = comment.get("user") or {}
                author = user.get("name", "User")
                body = comment.get("body", "")
                body_image_urls = extract_image_urls(body)
                if body_image_urls:
                    image_urls.extend(body_image_urls)
                    image_urls_by_comment_id[str(comment.get("id", ""))] = body_image_urls
                    logger.debug(
                        "Found %d image URL(s) in comment by %s",
                        len(body_image_urls),
                        author,
                    )
                if any(body.startswith(prefix) for prefix in BOT_MESSAGE_PREFIXES):
                    continue
                included_comments.append(comment)

    if triggering_comment and triggering_comment_id not in comment_ids:
        trigger_author = comment_author.get("name", "Unknown")
        trigger_body = triggering_comment
        trigger_image_urls = extract_image_urls(trigger_body)
        if trigger_image_urls:
            image_urls.extend(trigger_image_urls)
            image_urls_by_comment_id[str(triggering_comment_id)] = trigger_image_urls
            logger.debug(
                "Found %d image URL(s) in triggering comment by %s",
                len(trigger_image_urls),
                trigger_author,
            )
        included_comments.append(
            {
                "id": triggering_comment_id,
                "body": trigger_body,
                "user": comment_author,
            }
        )
        logger.debug(
            "Appended triggering comment %s not present in issue comments list",
            triggering_comment_id or "<missing-id>",
        )

    identifier = full_issue.get("identifier", "") or issue_data.get("identifier", "")
    ticket_url = full_issue.get("url", "") or issue_data.get("url", "")
    ticket_url_line = f"## Linear Ticket URL: {ticket_url}\n\n" if ticket_url else ""

    triggered_by_line = f"## Triggered by: {user_name}\n\n" if user_name else ""
    tag_instruction = (
        f"When calling linear_comment, tag @{user_name} if you are asking them a question, need their input, or are notifying them of something important (e.g. a completed PR). For simple answers, tagging is not required."
        if user_name
        else ""
    )
    prompt = (
        f"Please work on the following issue:\n\n"
        f"## Repository: {repo_config.get('owner')}/{repo_config.get('name')}\n\n"
        f"## Title: {title}\n\n"
        f"{triggered_by_line}"
        f"## Linear Ticket: {identifier} - Ticket ID: {issue_id}\n\n"
        f"{ticket_url_line}"
        f"## Description:\n{description}\n\n"
        "Please analyze this issue and implement the necessary changes. "
        "If you open a PR for this issue, make sure the PR description links back to "
        "this Linear ticket and follows this repository's PR conventions for the title, body, "
        "release note, and/or changelog. Inspect AGENTS.md, PR templates, "
        ".changelog/README.md, and nearby docs before choosing the PR title/body format. "
        f"When you're done, commit and push your changes. {tag_instruction}"
    )
    description_blocks: list[dict[str, Any]] = [cast(dict[str, Any], create_text_block(prompt))]
    image_blocks_by_url: dict[str, dict[str, Any]] = {}

    # Resolve the GitHub login from the Linear email via the same user-mapping
    # store Slack uses, so PRs open *as the triggering user* and the thread is
    # tagged for the dashboard.
    mapped_login = await resolve_login_from_email_async(user_email) if user_email else None

    image_model_override: tuple[str, str] | None = None
    if image_urls:
        image_urls = dedupe_urls(image_urls)
        resolved_model_id = await resolve_agent_model_id(mapped_login)
        if not model_supports_images(resolved_model_id):
            fallback_model_id, fallback_effort = default_vision_model_pair()
            logger.info(
                "Using vision fallback model %s for %d Linear image(s); configured model %s "
                "does not support images",
                fallback_model_id,
                len(image_urls),
                resolved_model_id,
            )
            resolved_model_id = fallback_model_id
            image_model_override = (fallback_model_id, fallback_effort)
        logger.info("Preparing %d image(s) for multimodal content", len(image_urls))
        logger.debug("Image URLs: %s", image_urls)

        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            for image_url in image_urls:
                image_block = await fetch_image_block(image_url, client)
                if image_block:
                    image_blocks_by_url[image_url] = cast(dict[str, Any], image_block)
        description_blocks.extend(
            image_blocks_by_url[url]
            for url in dedupe_urls(description_image_urls)
            if url in image_blocks_by_url
        )
        logger.info("Built %d description content block(s)", len(description_blocks))

    linear_project_id = ""
    linear_issue_number = ""
    if identifier and "-" in identifier:
        parts = identifier.split("-", 1)
        linear_project_id = parts[0]
        linear_issue_number = parts[1]

    configurable: dict[str, Any] = {
        "repo": repo_config,
        "linear_issue": {
            "id": issue_id,
            "title": title,
            "url": full_issue.get("url", "") or issue_data.get("url", ""),
            "identifier": identifier,
            "linear_project_id": linear_project_id,
            "linear_issue_number": linear_issue_number,
            "triggering_user_name": user_name or "",
        },
        "user_email": user_email,
        "source": "linear",
    }
    if mapped_login:
        configurable["github_login"] = mapped_login
    if image_model_override:
        configurable["agent_model_id"] = image_model_override[0]
        configurable["agent_effort"] = image_model_override[1]

    await upsert_agent_thread_owner_metadata(
        thread_id,
        source="linear",
        repo_config=repo_config,
        github_login=mapped_login or "",
        user_email=user_email or "",
        title=title or identifier or "Linear issue",
        source_context={"linear_issue": configurable["linear_issue"]},
    )

    run_messages = [
        system_introduction(
            {"id": "system:linear-issue", "display_name": "Linear issue", "platform": "linear"}
        ),
        system_input(
            description_blocks if len(description_blocks) > 1 else prompt,
            {
                "sender_id": "system:linear-issue",
                "surface": "linear",
                "kind": "system",
                "data": {
                    "issue": {
                        "id": issue_id,
                        "identifier": identifier,
                        "url": ticket_url,
                        "repository": f"{repo_config.get('owner')}/{repo_config.get('name')}",
                        "title": title,
                    }
                },
            },
        ),
    ]
    introduced: set[str] = set()
    for comment in included_comments:
        author = comment.get("user") or {}
        author_key = author.get("id") or author.get("email") or author.get("name") or "unknown"
        sender_id = f"linear:{str(author_key).replace(' ', '-')}"
        person: PersonIdentity = {"id": sender_id, "platform": "linear"}
        if author.get("name"):
            person["display_name"] = str(author["name"])
        if author.get("email"):
            person["email"] = str(author["email"])
        if sender_id not in introduced:
            run_messages.append(person_introduction(person))
            introduced.add(sender_id)
        body = str(comment.get("body", ""))
        comment_image_blocks = [
            image_blocks_by_url[url]
            for url in dedupe_urls(image_urls_by_comment_id.get(str(comment.get("id", "")), []))
            if url in image_blocks_by_url
        ]
        blocks: str | list[dict[str, Any]] = body
        if comment_image_blocks:
            blocks = [
                cast(dict[str, Any], create_text_block(body)),
                *comment_image_blocks,
            ]
        run_messages.append(
            human_input(
                blocks,
                {
                    "sender_id": sender_id,
                    "surface": "linear",
                    "kind": "human",
                    "data": {"comment_id": str(comment.get("id", ""))},
                },
            )
        )
    run_input: RunInput = {"messages": run_messages}
    run = await dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source="linear",
        input=run_input,
        metadata=agent_version_metadata(),
    )
    logger.info(
        "LangGraph run dispatched for thread %s (run=%s)",
        thread_id,
        run.get("run_id") if isinstance(run, dict) else None,
    )
    await post_linear_trace_comment(issue_id, thread_id, triggering_comment_id)
