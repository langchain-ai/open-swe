"""Linear webhook handler — moved out of common.py (behavior-identical).

Helpers and constants stay in common.py; they are accessed through the module
object (``common.X``) so tests that monkeypatch them keep working.
"""

from collections.abc import Mapping
from typing import Any

import httpx2

from openswe.attachments import Attachment, envelope_data
from openswe.attachments import stage as stage_attachments
from openswe.input_messages import (
    FieldValue,
    PersonIdentity,
    RunInput,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from openswe.prompts import prompt
from openswe.source_context import SourceContext
from openswe.thread_ids import linear_issue_thread_id
from openswe.users import User
from openswe.webhooks import common


def _linear_person(author: dict[str, Any]) -> PersonIdentity:
    key = author.get("id") or author.get("email") or author.get("name") or "unknown"
    person: PersonIdentity = {"id": f"linear:{str(key).replace(' ', '-')}"}
    if author.get("name"):
        person["display_name"] = str(author["name"])
    if author.get("email"):
        person["email"] = str(author["email"])
    return person


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

    thread_id = linear_issue_thread_id(issue_id)

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
    included_comments: list[dict[str, Any]] = []
    image_urls_by_comment_id: dict[str, list[str]] = {}
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
            for comment in relevant_comments:
                user = comment.get("user") or {}
                author = user.get("name", "User")
                body = comment.get("body", "")
                body_image_urls = common.extract_image_urls(body)
                if body_image_urls:
                    image_urls.extend(body_image_urls)
                    image_urls_by_comment_id[str(comment.get("id", ""))] = body_image_urls
                    common.logger.debug(
                        "Found %d image URL(s) in comment by %s",
                        len(body_image_urls),
                        author,
                    )
                if any(body.startswith(prefix) for prefix in bot_message_prefixes):
                    continue
                included_comments.append(comment)

    if triggering_comment and triggering_comment_id not in comment_ids:
        trigger_author = comment_author.get("name", "Unknown")
        trigger_body = triggering_comment
        trigger_image_urls = common.extract_image_urls(trigger_body)
        if trigger_image_urls:
            image_urls.extend(trigger_image_urls)
            image_urls_by_comment_id[str(triggering_comment_id)] = trigger_image_urls
            common.logger.debug(
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
        common.logger.debug(
            "Appended triggering comment %s not present in issue comments list",
            triggering_comment_id or "<missing-id>",
        )

    identifier = full_issue.get("identifier", "") or issue_data.get("identifier", "")
    ticket_url = full_issue.get("url", "") or issue_data.get("url", "")
    issue_prompt = prompt(
        "runs/linear-issue",
        repository=f"{repo_config.get('owner')}/{repo_config.get('name')}",
        title=title,
        triggered_by=user_name,
        identifier=identifier,
        issue_id=issue_id,
        ticket_url=ticket_url,
        description=description,
    )

    # Resolve the GitHub login from the Linear email the same way Slack does, so
    # PRs open *as the triggering user* and the thread is tagged for the dashboard.
    mapped_login = await User.login_for_email(user_email) if user_email else None
    # A follow-up stays in its thread's workspace; a new issue lands in the
    # repository's preferred one.
    workspace = await common.get_thread_workspace(
        thread_id
    ) or await common.workspace_for_repo_config(repo_config)

    image_urls = common.dedupe_urls(image_urls)
    fetched: dict[str, Attachment] = {}
    if image_urls:
        async with httpx2.AsyncClient(timeout=common.DEFAULT_HTTP_TIMEOUT) as client:
            for image_url in image_urls:
                if attachment := await common.fetch_attachment(image_url, client):
                    fetched[image_url] = attachment

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

    configurable["workspace"] = workspace
    configurable["environment"] = workspace

    await common.upsert_agent_thread_metadata(
        thread_id,
        source="linear",
        repo_config=repo_config,
        github_login=mapped_login or "",
        user_email=user_email or "",
        title=title or identifier or "Linear issue",
        source_context=SourceContext.parse({"linear_issue": configurable["linear_issue"]}),
        workspace=workspace,
    )
    staged = dict(
        zip(
            fetched,
            await stage_attachments(thread_id, list(fetched.values()), workspace_slug=workspace),
            strict=True,
        )
    )

    def attachment_data(urls: list[str]) -> dict[str, Mapping[str, FieldValue | None]]:
        return envelope_data(
            [staged[url].fields() for url in common.dedupe_urls(urls) if url in staged]
        )

    run_messages = [
        system_introduction(
            {"id": "system:linear-issue", "display_name": "Linear issue", "platform": "linear"}
        ),
        system_input(
            issue_prompt,
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
                    },
                    **attachment_data(description_image_urls),
                },
            },
        ),
    ]
    # The last comment triggers the run, and the run describes its author itself.
    introduced: set[str] = {
        _linear_person(comment.get("user") or {})["id"] for comment in included_comments[-1:]
    }
    for comment in included_comments:
        person = _linear_person(comment.get("user") or {})
        sender_id = person["id"]
        if sender_id not in introduced:
            run_messages.append(person_introduction(person))
            introduced.add(sender_id)
        comment_id = str(comment.get("id", ""))
        run_messages.append(
            human_input(
                str(comment.get("body", "")),
                {
                    "sender_id": sender_id,
                    "surface": "linear",
                    "kind": "human",
                    "data": {
                        "comment_id": comment_id,
                        **attachment_data(image_urls_by_comment_id.get(comment_id, [])),
                    },
                },
            )
        )
    run_input: RunInput = {"messages": run_messages}
    run = await common.dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source="linear",
        thread_title=None,
        input=run_input,
        metadata=common.AGENT_VERSION_METADATA,
    )
    common.logger.info(
        "LangGraph run dispatched for thread %s (run=%s)",
        thread_id,
        run.get("run_id") if isinstance(run, dict) else None,
    )
