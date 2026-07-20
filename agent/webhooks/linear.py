"""Linear webhook handler — moved out of common.py (behavior-identical).

Helpers and constants stay in common.py; they are accessed through the module
object (``common.X``) so tests that monkeypatch them keep working.
"""

from typing import Any, cast

import httpx
from langchain_core.messages.content import create_text_block

from . import common


async def process_linear_issue(  # noqa: PLR0912, PLR0915
    issue_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    """Process a Linear issue by creating a new LangGraph thread and run.

    Args:
        issue_data: The Linear issue data from webhook (basic info only).
        repo_config: The repo configuration with owner and name.
    """
    issue_id = issue_data.get("id", "")
    common.logger.info(
        "Processing Linear issue %s for repo %s/%s",
        issue_id,
        repo_config.get("owner"),
        repo_config.get("name"),
    )

    triggering_comment_id = issue_data.get("triggering_comment_id", "")
    if triggering_comment_id:
        await common.react_to_linear_comment(triggering_comment_id, "👀")

    thread_id = common.generate_thread_id_from_issue(issue_id)

    full_issue = await common.fetch_linear_issue_details(issue_id)
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

    common.logger.info("User email for issue %s: %s", issue_id, user_email)

    title = full_issue.get("title", "No title")
    description = full_issue.get("description") or "No description"
    image_urls: list[str] = []
    description_image_urls = common.extract_image_urls(description)
    if description_image_urls:
        image_urls.extend(description_image_urls)
        common.logger.debug(
            "Found %d image URL(s) in issue description",
            len(description_image_urls),
        )

    comments = full_issue.get("comments", {}).get("nodes", [])
    comments_text = ""
    triggering_comment = issue_data.get("triggering_comment", "")
    triggering_comment_id = issue_data.get("triggering_comment_id", "")

    bot_message_prefixes = (
        "🔐 **GitHub Authentication Required**",
        "✅ **Pull Request Created**",
        "✅ **Pull Request Updated**",
        "**Pull Request Created**",
        "**Pull Request Updated**",
        "🤖 **Agent Response**",
        "❌ **Agent Error**",
    )

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
            common.logger.debug(
                "Using triggering comment index %d to build relevant comments",
                trigger_index,
            )
        else:
            relevant_comments = common.get_recent_comments(comments, bot_message_prefixes)

        if relevant_comments:
            comments_text = "\n\n## Comments:\n"
            for comment in relevant_comments:
                user = comment.get("user") or {}
                author = user.get("name", "User")
                body = comment.get("body", "")
                body_image_urls = common.extract_image_urls(body)
                if body_image_urls:
                    image_urls.extend(body_image_urls)
                    common.logger.debug(
                        "Found %d image URL(s) in comment by %s",
                        len(body_image_urls),
                        author,
                    )
                if any(body.startswith(prefix) for prefix in bot_message_prefixes):
                    continue
                comments_text += f"\n**{author}:** {body}\n"

    if triggering_comment and triggering_comment_id not in comment_ids:
        if not comments_text:
            comments_text = "\n\n## Comments:\n"
        trigger_author = comment_author.get("name", "Unknown")
        trigger_body = triggering_comment
        trigger_image_urls = common.extract_image_urls(trigger_body)
        if trigger_image_urls:
            image_urls.extend(trigger_image_urls)
            common.logger.debug(
                "Found %d image URL(s) in triggering comment by %s",
                len(trigger_image_urls),
                trigger_author,
            )
        comments_text += f"\n**{trigger_author}:** {trigger_body}\n"
        common.logger.debug(
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
        f"## Description:\n{description}\n"
        f"{comments_text}\n\n"
        "Please analyze this issue and implement the necessary changes. "
        "If you open a PR for this issue, make sure the PR description links back to "
        "this Linear ticket and follows this repository's PR conventions for the title, body, "
        "release note, and/or changelog. Inspect AGENTS.md, PR templates, "
        ".changelog/README.md, and nearby docs before choosing the PR title/body format. "
        f"When you're done, commit and push your changes. {tag_instruction}"
    )
    content_blocks: list[dict[str, Any]] = [cast(dict[str, Any], create_text_block(prompt))]

    # Resolve the GitHub login from the Linear email via the same user-mapping
    # store Slack uses, so PRs open *as the triggering user* and the thread is
    # tagged for the dashboard.
    mapped_login = await common.resolve_login_from_email_async(user_email) if user_email else None

    image_model_override: tuple[str, str] | None = None
    if image_urls:
        image_urls = common.dedupe_urls(image_urls)
        resolved_model_id = await common.resolve_agent_model_id(mapped_login)
        if not common.model_supports_images(resolved_model_id):
            fallback_model_id, fallback_effort = common.default_vision_model_pair()
            common.logger.info(
                "Using vision fallback model %s for %d Linear image(s); configured model %s "
                "does not support images",
                fallback_model_id,
                len(image_urls),
                resolved_model_id,
            )
            resolved_model_id = fallback_model_id
            image_model_override = (fallback_model_id, fallback_effort)
        common.logger.info("Preparing %d image(s) for multimodal content", len(image_urls))
        common.logger.debug("Image URLs: %s", image_urls)

        async with httpx.AsyncClient(timeout=common.DEFAULT_HTTP_TIMEOUT) as client:
            for image_url in image_urls:
                image_block = await common.fetch_image_block(image_url, client)
                if image_block:
                    content_blocks.append(cast(dict[str, Any], image_block))
        common.logger.info("Built %d content block(s) for prompt", len(content_blocks))

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

    await common.upsert_agent_thread_owner_metadata(
        thread_id,
        source="linear",
        repo_config=repo_config,
        github_login=mapped_login or "",
        user_email=user_email or "",
        title=title or identifier or "Linear issue",
        source_context={"linear_issue": configurable["linear_issue"]},
    )

    run = await common.dispatch_agent_run(
        thread_id,
        content_blocks,
        configurable,
        source="linear",
        metadata=common._AGENT_VERSION_METADATA,
    )
    common.logger.info(
        "LangGraph run dispatched for thread %s (run=%s)",
        thread_id,
        run.get("run_id") if isinstance(run, dict) else None,
    )
    await common.post_linear_trace_comment(issue_id, thread_id, triggering_comment_id)
