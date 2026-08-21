"""GitHub webhook handlers.

Everything here is payload adaptation: parse what GitHub sent, resolve the
thread and token it belongs to, and hand off. Reviewer runs are owned by
``agent.review.dispatch``; CI watches by ``agent.baby_sit``.
"""

import logging
from typing import Any

from ..baby_sit import handle_ci_webhook
from ..config import agent_version_metadata, langgraph_client
from ..dashboard.user_mappings import email_for_login
from ..dispatch import dispatch_agent_run
from ..input_messages import (
    RunInput,
    SystemIdentity,
    github_person,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from ..review.dispatch import (
    PullRequestTarget,
    review_opened_pull_request,
    review_pushed_branch,
    route_finding_reply,
    update_pr_watch,
)
from ..review.findings import REVIEWER_THREAD_KIND
from ..thread_ids import github_issue_thread_id, pr_comment_thread_id, thread_id_from_branch
from ..utils.auth import is_bot_token_only_mode, resolve_github_token_from_email
from ..utils.github_app import (
    get_github_app_installation_token,
    get_github_app_installation_token_with_expiry,
)
from ..utils.github_comments import (
    GitHubAuthError,
    build_pr_prompt,
    derive_pr_state,
    extract_pr_context,
    fetch_issue_comments,
    fetch_pr_comments_since_last_tag,
    format_github_comment_body_for_prompt,
    react_to_github_comment,
    sanitize_github_comment_body,
)
from ..utils.github_token import (
    cache_github_token_for_thread,
    get_github_token_from_thread,
    github_token_principal,
    invalidate_cached_github_token,
)
from ..utils.thread_ops import (
    is_not_found_error,
    thread_exists,
    upsert_agent_thread_owner_metadata,
)

logger = logging.getLogger(__name__)

_GITHUB_BOT_MESSAGE_PREFIXES = (
    "🔐 **GitHub Authentication Required**",
    "✅ **Pull Request Created**",
    "✅ **Pull Request Updated**",
    "**Pull Request Created**",
    "**Pull Request Updated**",
    "🤖 **Agent Response**",
    "❌ **Agent Error**",
)


def _repo_config_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    repo = payload.get("repository") or {}
    owner = repo.get("owner") or {}
    return {
        "owner": owner.get("login", "") or owner.get("name", ""),
        "name": repo.get("name", ""),
    }


def _repo_private_from_payload(payload: dict[str, Any]) -> bool | None:
    repo = payload.get("repository")
    private = repo.get("private") if isinstance(repo, dict) else None
    return private if isinstance(private, bool) else None


def _repo_id_from_payload(payload: dict[str, Any]) -> int | None:
    repo = payload.get("repository")
    repo_id = repo.get("id") if isinstance(repo, dict) else None
    return repo_id if isinstance(repo_id, int) else None


def review_comment_reply_parent_id(payload: dict[str, Any]) -> int | None:
    """The comment a review-comment payload replies to, if it is a reply at all."""
    comment = payload.get("comment")
    if not isinstance(comment, dict):
        return None
    parent_id = comment.get("in_reply_to_id")
    return parent_id if isinstance(parent_id, int) else None


def _pull_request_target_from_payload(payload: dict[str, Any]) -> PullRequestTarget:
    """The pull request a ``pull_request`` / review-comment payload is about."""
    repo_config = _repo_config_from_payload(payload)
    pull_request = payload.get("pull_request") or {}
    base = pull_request.get("base") or {}
    head = pull_request.get("head") or {}
    number = pull_request.get("number")
    return PullRequestTarget(
        owner=repo_config["owner"],
        name=repo_config["name"],
        number=number if isinstance(number, int) else 0,
        url=pull_request.get("html_url", "") or pull_request.get("url", ""),
        title=pull_request.get("title", ""),
        head_ref=head.get("ref", ""),
        base_ref=base.get("ref", ""),
        base_sha=base.get("sha", ""),
        head_sha=head.get("sha", ""),
        author=(pull_request.get("user") or {}).get("login", ""),
        private=_repo_private_from_payload(payload),
        repo_id=_repo_id_from_payload(payload),
    )


def build_github_issue_prompt(
    repo_config: dict[str, str],
    issue_number: int,
    issue_id: str,
    title: str,
    body: str,
    comments: list[dict[str, Any]],
    *,
    github_login: str,
    issue_author: str = "",
    issue_url: str = "",
) -> str:
    """Build the user prompt for a GitHub issue-triggered run."""
    triggered_by_line = f"## Triggered by: {github_login}\n\n" if github_login else ""
    issue_url_line = f"## Issue URL: {issue_url}\n\n" if issue_url else ""
    comments_text = _build_github_issue_comments_text(comments)
    sanitized_title = sanitize_github_comment_body(title)
    formatted_body = format_github_comment_body_for_prompt(issue_author or github_login, body)
    return (
        "Please work on the following GitHub issue:\n\n"
        f"## Repository: {repo_config.get('owner')}/{repo_config.get('name')}\n\n"
        f"{triggered_by_line}"
        f"## GitHub Issue: #{issue_number} - Issue ID: {issue_id}\n\n"
        f"{issue_url_line}"
        f"## Title: {sanitized_title}\n\n"
        f"## Description:\n{formatted_body}\n"
        f"{comments_text}\n\n"
        "Please analyze this issue and implement the necessary changes. "
        "If you open a PR for this issue, make sure the PR description links back to "
        "this issue and follows this repository's PR conventions for the title, body, "
        "release note, and/or changelog. Inspect AGENTS.md, PR templates, "
        ".changelog/README.md, and nearby docs before choosing the PR title/body format. "
        "When you need to communicate on GitHub, use `gh issue comment` "
        "with the issue number."
    )


def build_github_issue_followup_prompt(github_login: str, comment_body: str) -> str:
    """Build the prompt for a follow-up GitHub issue comment."""
    return (
        f"**{github_login}:**\n{format_github_comment_body_for_prompt(github_login, comment_body)}"
    )


def build_github_issue_update_prompt(github_login: str, title: str, body: str) -> str:
    """Build the prompt for a follow-up GitHub issue title/body update."""
    sanitized_title = sanitize_github_comment_body(title)
    formatted_body = format_github_comment_body_for_prompt(github_login, body)
    return (
        f"**{github_login}:** updated the GitHub issue title/body.\n\n"
        f"Title: {sanitized_title}\n\n"
        f"Description:\n{formatted_body}"
    )


def _build_github_issue_comments_text(comments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for comment in comments:
        body = comment.get("body", "")
        if not body or any(body.startswith(prefix) for prefix in _GITHUB_BOT_MESSAGE_PREFIXES):
            continue
        author = comment.get("author", "unknown")
        formatted_body = format_github_comment_body_for_prompt(author, body)
        lines.append(f"\n**{author}:**\n{formatted_body}\n")

    if not lines:
        return ""
    return "\n\n## Comments:\n" + "".join(lines)


def _github_human_run_input(
    login: str,
    content: str,
    *,
    user_id: object = None,
    data: dict[str, object] | None = None,
) -> RunInput:
    person = github_person(login, user_id)
    return {
        "messages": [
            person_introduction(person),
            human_input(
                content,
                {
                    "sender_id": person["id"],
                    "surface": "github",
                    "kind": "human",
                    "data": data or {},
                },
            ),
        ]
    }


def _github_issue_run_input(
    prompt: str,
    comments: list[dict[str, Any]],
    *,
    issue_author: str,
    description: str,
    trigger_login: str,
    trigger_user_id: object,
    issue_data: dict[str, object],
) -> RunInput:
    actor: SystemIdentity = {
        "id": "system:github-webhook",
        "display_name": "GitHub webhook",
        "platform": "github",
    }
    messages = [
        system_introduction(actor),
        system_input(
            prompt,
            {
                "sender_id": actor["id"],
                "surface": "github",
                "kind": "system",
                "data": {"issue": issue_data},
            },
        ),
    ]
    introduced: set[str] = set()
    source_messages = [
        {"author": issue_author or trigger_login, "body": description, "type": "description"},
        *comments,
    ]
    for comment in source_messages:
        author = str(comment.get("author") or "unknown")
        person = github_person(
            author, trigger_user_id if author == trigger_login else comment.get("author_id")
        )
        if person["id"] not in introduced:
            messages.append(person_introduction(person))
            introduced.add(person["id"])
        body = format_github_comment_body_for_prompt(author, str(comment.get("body", "")))
        messages.append(
            human_input(
                body,
                {
                    "sender_id": person["id"],
                    "surface": "github",
                    "kind": "human",
                    "data": {
                        "message_type": str(comment.get("type", "comment")),
                        "created_at": str(comment.get("created_at", "")),
                        "comment_id": str(comment.get("comment_id", "")),
                    },
                },
            )
        )
    return {"messages": messages}


async def process_github_pr_ready(payload: dict[str, Any]) -> None:
    """Auto-review a PR that has just been opened or marked ready-for-review."""
    pull_request = payload.get("pull_request", {})
    sender = payload.get("sender", {})
    # Use source="github" so the reviewer resolver can use the GitHub App token;
    # "github_auto" would fall through to the email-based path, which has no
    # user_email to route on for webhook-triggered runs.
    await review_opened_pull_request(
        _pull_request_target_from_payload(payload),
        source="github",
        actor_login=sender.get("login", ""),
        actor_user_id=sender.get("id"),
        is_draft=bool(pull_request.get("draft")),
        ready_for_review=payload.get("action") == "ready_for_review",
    )


async def process_github_pr_close(payload: dict[str, Any]) -> None:
    """Toggle reviewer watch on a PR close / reopen / draft transition."""
    pull_request = payload.get("pull_request", {})
    pr_number = pull_request.get("number")
    if not isinstance(pr_number, int) or not pr_number:
        return
    author = pull_request.get("user") or {}
    await update_pr_watch(
        _repo_config_from_payload(payload),
        pr_number,
        action=payload.get("action", ""),
        author_login=author.get("login", "") if isinstance(author, dict) else "",
    )


async def process_github_push_event(payload: dict[str, Any]) -> None:
    """Re-trigger the reviewer for a watched PR when its head branch is pushed to."""
    ref = payload.get("ref", "")
    after_sha = payload.get("after", "")
    if not ref.startswith("refs/heads/"):
        logger.debug("Push ignored: ref %s is not a branch", ref)
        return
    if not isinstance(after_sha, str) or not after_sha or set(after_sha) == {"0"}:
        logger.debug("Push to %s ignored: branch deletion or missing SHA", ref)
        return

    sender = payload.get("sender", {})
    await review_pushed_branch(
        _repo_config_from_payload(payload),
        branch=ref[len("refs/heads/") :],
        after_sha=after_sha,
        repo_private=_repo_private_from_payload(payload),
        repo_id=_repo_id_from_payload(payload),
        actor_login=sender.get("login", "") or "",
        actor_user_id=sender.get("id"),
    )


async def process_github_review_finding_reply(payload: dict[str, Any]) -> None:
    """Route replies to Open SWE review comments back to the reviewer graph."""
    parent_comment_id = review_comment_reply_parent_id(payload)
    if parent_comment_id is None:
        return

    sender = payload.get("sender", {})
    sender_login = sender.get("login") if isinstance(sender, dict) else None
    if sender_login == "open-swe[bot]":
        return

    target = _pull_request_target_from_payload(payload)
    if not target.number:
        return

    comment = payload.get("comment", {})
    reply_body = comment.get("body")
    created_at = comment.get("created_at")
    comment_id = comment.get("id")
    await route_finding_reply(
        target,
        parent_comment_id=parent_comment_id,
        reply_author=sender_login if isinstance(sender_login, str) else "unknown",
        reply_user_id=sender.get("id") if isinstance(sender, dict) else None,
        reply_body=reply_body if isinstance(reply_body, str) else "",
        reply_comment_id=comment_id if isinstance(comment_id, int) else None,
        reply_created_at=created_at if isinstance(created_at, str) else "",
    )


async def process_github_ci_event(
    payload: dict[str, Any], event_type: str, delivery_id: str | None = None
) -> None:
    """Evaluate active baby-sit watches for a signed GitHub CI event."""
    await handle_ci_webhook(payload, event_type, delivery_id=delivery_id)


def _pr_state_from_payload(payload: dict[str, Any]) -> str | None:
    pull_request = payload.get("pull_request") if isinstance(payload, dict) else None
    if not isinstance(pull_request, dict):
        return None
    state = pull_request.get("state")
    return derive_pr_state(
        state=state if isinstance(state, str) else None,
        merged=bool(pull_request.get("merged")),
        draft=bool(pull_request.get("draft")),
    )


async def update_agent_thread_pr_state(payload: dict[str, Any]) -> None:
    """Keep an agent thread's tracked PR state in sync with PR lifecycle events.

    The agent thread is located by the PR's html_url persisted in metadata when
    the PR was opened (``open_pull_request``). Reviewer threads are skipped.
    """
    pull_request = payload.get("pull_request") if isinstance(payload, dict) else None
    if not isinstance(pull_request, dict):
        return
    pr_url = pull_request.get("html_url")
    new_state = _pr_state_from_payload(payload)
    if not isinstance(pr_url, str) or not pr_url or new_state is None:
        return

    client = langgraph_client()
    matching_threads: dict[str, Any] = {}
    page_size = 50
    for metadata_filter in ({"pr_url": pr_url}, {"pr_urls": [pr_url]}):
        offset = 0
        while True:
            try:
                threads = await client.threads.search(
                    metadata=metadata_filter, limit=page_size, offset=offset
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Could not search threads for PR %s state update", pr_url, exc_info=True
                )
                break
            page = threads or []
            for thread in page:
                thread_id = (
                    (thread.get("thread_id") or thread.get("id"))
                    if isinstance(thread, dict)
                    else None
                )
                if isinstance(thread_id, str) and thread_id:
                    matching_threads[thread_id] = thread
            if len(page) < page_size:
                break
            offset += page_size

    for thread_id, thread in matching_threads.items():
        metadata = thread.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("kind") == REVIEWER_THREAD_KIND:
            continue
        metadata_update: dict[str, Any] = {}
        pull_requests = metadata.get("pull_requests")
        if isinstance(pull_requests, list):
            updated = [
                {**record, "state": new_state} if record.get("url") == pr_url else record
                for record in pull_requests
                if isinstance(record, dict)
            ]
            if updated != pull_requests:
                metadata_update["pull_requests"] = updated
        if metadata.get("pr_url") == pr_url and metadata.get("pr_state") != new_state:
            metadata_update["pr_state"] = new_state
        if not metadata_update:
            continue
        try:
            await client.threads.update(thread_id=thread_id, metadata=metadata_update)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to update pr_state for thread %s", thread_id, exc_info=True)


async def _refresh_thread_github_token_after_401(thread_id: str, email: str) -> str | None:
    """Invalidate the cached token after a 401 and try to resolve a fresh one."""
    logger.warning(
        "GitHub returned 401 for thread %s; invalidating cached token and re-resolving",
        thread_id,
    )
    await invalidate_cached_github_token(thread_id)
    return await _get_or_resolve_thread_github_token(thread_id, email)


async def _get_or_resolve_thread_github_token(thread_id: str, email: str) -> str | None:
    """Resolve and cache a GitHub token for a thread when available.

    In bot-token-only mode, returns a fresh GitHub App installation token
    instead of resolving per-user OAuth tokens.
    """
    if is_bot_token_only_mode():
        bot_token, expires_at = await get_github_app_installation_token_with_expiry()
        if bot_token:
            cache_github_token_for_thread(
                thread_id, bot_token, expires_at=expires_at, is_bot_token=True
            )
            return bot_token
        logger.warning("Bot-token-only mode but GitHub App token unavailable")
        return None

    principal = github_token_principal(email=email)
    github_token, _expires_at = await get_github_token_from_thread(thread_id, principal=principal)
    if github_token:
        return github_token

    auth_result = await resolve_github_token_from_email(email)
    github_token = auth_result.get("token")
    if not github_token:
        return None

    expires_at = auth_result.get("expires_at")
    cache_github_token_for_thread(
        thread_id,
        github_token,
        expires_at=expires_at if isinstance(expires_at, str) else None,
        principal=principal,
    )
    return github_token


async def _trigger_or_queue_run(
    thread_id: str,
    prompt: str,
    *,
    input: Any | None = None,
    github_login: str,
    github_user_id: int | None,
    repo_config: dict[str, str],
    pr_number: int,
) -> None:
    """Create a new agent run or queue the message if the thread is busy."""
    await upsert_agent_thread_owner_metadata(
        thread_id,
        source="github",
        repo_config=repo_config,
        github_login=github_login,
        title=f"PR #{pr_number}" if pr_number else "",
        source_context={"pr_number": pr_number} if pr_number else None,
    )
    logger.info("Dispatching LangGraph run for thread %s from GitHub PR comment", thread_id)
    await dispatch_agent_run(
        thread_id,
        None if input is not None else prompt,
        {
            "source": "github",
            "github_login": github_login,
            "github_user_id": github_user_id,
            "repo": repo_config,
            "pr_number": pr_number,
        },
        source="github",
        input=input,
        metadata=agent_version_metadata(),
    )
    logger.info("LangGraph run created for thread %s from GitHub PR comment", thread_id)


async def process_github_pr_comment(payload: dict[str, Any], event_type: str) -> None:
    """Process a GitHub PR comment that tagged @open-swe.

    Retrieves the existing thread token, reacts with 👀, fetches all comments
    since the last @open-swe tag, then creates or queues a new run.

    Args:
        payload: The parsed GitHub webhook payload.
        event_type: One of 'issue_comment', 'pull_request_review_comment',
                    'pull_request_review'.
    """
    (
        repo_config,
        pr_number,
        branch_name,
        github_login,
        pr_url,
        comment_id,
        node_id,
    ) = await extract_pr_context(payload, event_type)
    github_user_id = payload.get("sender", {}).get("id")

    logger.info(
        "Processing GitHub PR comment: event=%s, pr=%s, branch=%s",
        event_type,
        pr_number,
        branch_name,
    )

    thread_id = thread_id_from_branch(branch_name) if branch_name else None
    if not thread_id:
        if not pr_number:
            logger.warning(
                "Could not determine thread_id for branch '%s' (no pr_number), skipping",
                branch_name,
            )
            return
        thread_id = pr_comment_thread_id(
            repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
        )
        logger.info("Generated thread_id %s for non-open-swe branch '%s'", thread_id, branch_name)
        client = langgraph_client()
        try:
            await client.threads.update(thread_id, metadata={"branch_name": branch_name})
        except Exception as exc:  # noqa: BLE001
            if is_not_found_error(exc):
                await client.threads.create(
                    thread_id=thread_id,
                    if_exists="do_nothing",
                    metadata={"branch_name": branch_name},
                )
            else:
                logger.warning("Failed to persist branch_name metadata for thread %s", thread_id)

    email = await email_for_login(github_login) or ""
    if email:
        github_token = await _get_or_resolve_thread_github_token(thread_id, email)
    else:
        logger.warning("No email mapping for GitHub user '%s', skipping", github_login)
        return

    if not github_token:
        logger.warning("No GitHub token for thread %s, skipping", thread_id)
        return

    if comment_id:
        try:
            await react_to_github_comment(
                repo_config,
                comment_id,
                event_type=event_type,
                token=github_token,
                pull_number=pr_number,
                node_id=node_id,
            )
        except GitHubAuthError:
            github_token = await _refresh_thread_github_token_after_401(thread_id, email)
            if not github_token:
                logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
                return
            await react_to_github_comment(
                repo_config,
                comment_id,
                event_type=event_type,
                token=github_token,
                pull_number=pr_number,
                node_id=node_id,
            )

    if not pr_number:
        logger.warning("No PR number found in payload, skipping")
        return

    try:
        comments = await fetch_pr_comments_since_last_tag(
            repo_config, pr_number, token=github_token
        )
    except GitHubAuthError:
        github_token = await _refresh_thread_github_token_after_401(thread_id, email)
        if not github_token:
            logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
            return
        comments = await fetch_pr_comments_since_last_tag(
            repo_config, pr_number, token=github_token
        )
    if not comments:
        logger.info("No comments found since last @open-swe tag for PR %s", pr_number)
        return

    prompt = build_pr_prompt(comments, pr_url, repo_config=repo_config)
    messages = []
    introduced: set[str] = set()
    for item in comments:
        author = str(item.get("author") or "unknown")
        person = github_person(author, github_user_id if author == github_login else None)
        if person["id"] not in introduced:
            messages.append(person_introduction(person))
            introduced.add(person["id"])
        messages.append(
            human_input(
                format_github_comment_body_for_prompt(author, str(item.get("body", ""))),
                {
                    "sender_id": person["id"],
                    "surface": "github",
                    "kind": "human",
                    "data": {
                        "pull_request": {"number": pr_number, "url": pr_url},
                        "comment_type": str(item.get("type", "comment")),
                        "path": str(item.get("path", "")),
                        "line": str(item.get("line", "")),
                        "created_at": str(item.get("created_at", "")),
                    },
                },
            )
        )
    await _trigger_or_queue_run(
        thread_id,
        prompt,
        input={"messages": messages},
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_number,
    )


async def process_github_issue(payload: dict[str, Any], event_type: str) -> None:
    """Process a GitHub issue or issue comment that tagged @open-swe."""
    issue = payload.get("issue", {})
    repo_config = _repo_config_from_payload(payload)

    issue_id = str(issue.get("id", ""))
    issue_number = issue.get("number")
    github_login = payload.get("sender", {}).get("login", "")
    github_user_id = payload.get("sender", {}).get("id")
    issue_url = issue.get("html_url", "") or issue.get("url", "")
    title = issue.get("title", "No title")
    description = issue.get("body") or "No description"
    issue_author = issue.get("user", {}).get("login", "")

    logger.info(
        "Processing GitHub issue: event=%s, issue=%s, repo=%s/%s",
        event_type,
        issue_number,
        repo_config.get("owner"),
        repo_config.get("name"),
    )

    if not issue_id or not issue_number:
        logger.warning("Missing GitHub issue id/number, skipping")
        return

    email = await email_for_login(github_login) or ""
    if not email:
        logger.warning("No email mapping for GitHub user '%s', skipping", github_login)
        return

    thread_id = github_issue_thread_id(issue_id)
    existing_thread = await thread_exists(thread_id)
    github_token = await _get_or_resolve_thread_github_token(thread_id, email)
    app_token = await get_github_app_installation_token()
    reaction_token = github_token or app_token
    comment = payload.get("comment", {})
    comment_id = comment.get("id")
    if event_type == "issue_comment" and comment_id:
        if not reaction_token:
            logger.warning("No GitHub token available to react to issue comment %s", comment_id)
        else:
            try:
                reacted = await react_to_github_comment(
                    repo_config,
                    comment_id,
                    event_type="issue_comment",
                    token=reaction_token,
                )
            except GitHubAuthError:
                github_token = await _refresh_thread_github_token_after_401(thread_id, email)
                reaction_token = github_token or app_token
                reacted = False
                if reaction_token:
                    try:
                        reacted = await react_to_github_comment(
                            repo_config,
                            comment_id,
                            event_type="issue_comment",
                            token=reaction_token,
                        )
                    except GitHubAuthError:
                        logger.warning(
                            "Re-auth still produced 401 reacting to issue comment %s",
                            comment_id,
                        )
                        reacted = False
            if not reacted:
                logger.warning("Failed to react to GitHub issue comment %s", comment_id)

    comments: list[dict[str, Any]] = []
    if existing_thread:
        if event_type == "issue_comment":
            prompt = build_github_issue_followup_prompt(
                comment.get("user", {}).get("login", github_login) or github_login,
                comment.get("body", ""),
            )
        else:
            prompt = build_github_issue_update_prompt(github_login, title, description)
    else:
        try:
            comments = await fetch_issue_comments(
                repo_config, issue_number, token=github_token or app_token
            )
        except GitHubAuthError:
            github_token = await _refresh_thread_github_token_after_401(thread_id, email)
            comments = await fetch_issue_comments(
                repo_config, issue_number, token=github_token or app_token
            )
        if comment_id and not any(item.get("comment_id") == comment_id for item in comments):
            comments.append(
                {
                    "body": comment.get("body", ""),
                    "author": comment.get("user", {}).get("login", "unknown"),
                    "created_at": comment.get("created_at", ""),
                    "comment_id": comment_id,
                }
            )
            comments.sort(key=lambda item: item.get("created_at", ""))

        prompt = build_github_issue_prompt(
            repo_config,
            issue_number,
            issue_id,
            title,
            description,
            comments,
            github_login=github_login,
            issue_author=issue_author,
            issue_url=issue_url,
        )
    configurable: dict[str, Any] = {
        "source": "github",
        "github_login": github_login,
        "github_user_id": github_user_id,
        "repo": repo_config,
        "github_issue": {
            "id": issue_id,
            "number": issue_number,
            "title": title,
            "url": issue_url,
        },
    }

    await upsert_agent_thread_owner_metadata(
        thread_id,
        source="github",
        repo_config=repo_config,
        github_login=github_login,
        title=title or (f"Issue #{issue_number}" if issue_number else ""),
        source_context={"github_issue": configurable["github_issue"]},
    )

    logger.info("Dispatching LangGraph run for thread %s from GitHub issue", thread_id)
    client = langgraph_client()
    if existing_thread:
        sender_login = (
            comment.get("user", {}).get("login", github_login) or github_login
            if event_type == "issue_comment"
            else github_login
        )
        run_input = _github_human_run_input(
            sender_login,
            prompt,
            user_id=github_user_id,
            data={
                "issue": {"id": issue_id, "number": issue_number, "url": issue_url},
                "event_type": event_type,
                "comment_id": comment_id or "",
            },
        )
    else:
        run_input = _github_issue_run_input(
            prompt,
            comments,
            issue_author=issue_author,
            description=description,
            trigger_login=github_login,
            trigger_user_id=github_user_id,
            issue_data={
                "id": issue_id,
                "number": issue_number,
                "url": issue_url,
                "repository": f"{repo_config.get('owner')}/{repo_config.get('name')}",
                "title": title,
            },
        )
    await dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source="github_issue",
        input=run_input,
        metadata=agent_version_metadata(),
        client=client,
    )
    logger.info("LangGraph run dispatched for thread %s from GitHub issue", thread_id)
