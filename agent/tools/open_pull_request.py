"""Open a GitHub pull request attributed to the triggering user."""

import logging
from typing import Any
from urllib.parse import quote

import httpx
from langgraph.config import get_config
from langgraph_sdk import get_client

from ..dashboard.agent_usage import record_agent_pr_usage
from ..dashboard.plan_store import get_plan_content
from ..utils.dashboard_links import dashboard_plan_url
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import derive_pr_state
from ..utils.slack import get_active_slack_thread, get_slack_permalink, parse_github_pr_url
from ..utils.slack_code_channels import (
    is_code_channel_session,
    repo_context_bar_items,
    set_context_bar,
    set_view,
)

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_USER_TOKEN_SOURCES = ("slack", "linear", "dashboard")
_REFERENCES_HEADING = "## References"
_ACCESS_FAILURE_CODE = "github_app_access_missing_or_repo_not_found"
_BRANCH_FAILURE_CODE = "github_pr_branch_not_visible"
_PREFLIGHT_FAILURE_CODE = "github_pr_preflight_failed"
_RESPONSE_BODY_LIMIT = 800
_REPORTED_RESPONSE_HEADERS = (
    "location",
    "retry-after",
    "www-authenticate",
    "x-accepted-github-permissions",
    "x-accepted-oauth-scopes",
    "x-oauth-scopes",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-github-request-id",
)


async def _resolve_pr_author_token() -> tuple[str | None, str]:
    """Return ``(token, kind)`` for opening the PR.

    Prefers the triggering user's OAuth token (so the PR is created *as them*)
    for Slack/Linear/dashboard runs with a mapped GitHub login, resolving it by
    login from the dashboard OAuth store. Falls back to the GitHub App
    installation token (creator = open-swe[bot]) for GitHub-triggered runs,
    unmapped users, or bot-token-only deployments — preserving today's behavior.

    The token is resolved by login rather than read from the shared thread
    metadata: Slack thread ids are shared across a conversation, so a cached
    token could belong to a prior triggering user.
    """
    configurable = get_config().get("configurable", {})
    source = configurable.get("source")
    github_login = configurable.get("github_login")

    if source in _USER_TOKEN_SOURCES and isinstance(github_login, str) and github_login.strip():
        from ..dashboard.profiles import get_valid_access_token

        user_token = await get_valid_access_token(github_login.strip())
        if user_token:
            return user_token, "user"
        logger.info("No valid user token for %s; opening PR as open-swe[bot]", github_login.strip())

    return await get_github_app_installation_token(), "bot"


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return resp.text.strip() or f"HTTP {resp.status_code}"
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return resp.text.strip() or f"HTTP {resp.status_code}"


def _github_response_summary(resp: httpx.Response | None) -> str:
    """Report what GitHub actually returned so the agent can diagnose it itself."""
    if resp is None:
        return ""
    # Diagnostics must never raise on an error path and mask the real failure.
    request = getattr(resp, "request", None)
    target = f" to {request.method} {request.url}" if request is not None else ""
    parts = [f"GitHub responded{target} with {resp.status_code}"]
    resp_headers = getattr(resp, "headers", None) or {}
    headers = [
        f"{name}: {resp_headers[name]}"
        for name in _REPORTED_RESPONSE_HEADERS
        if name in resp_headers
    ]
    if headers:
        parts.append("response headers: " + ", ".join(headers))
    body = (getattr(resp, "text", "") or "")[:_RESPONSE_BODY_LIMIT]
    if body:
        parts.append(f"response body: {body}")
    return " " + ". ".join(parts) + "."


def _effective_draft(draft: bool) -> bool:
    configurable = _configurable()
    preference = configurable.get("draft_prs")
    return preference if isinstance(preference, bool) else draft


def _configurable() -> dict[str, Any]:
    try:
        config = get_config()
    except Exception:
        return {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    return dict(configurable) if isinstance(configurable, dict) else {}


def _head_branch_for_repo(owner: str, head: str) -> str | None:
    if ":" not in head:
        return head
    head_owner, branch = head.split(":", 1)
    if head_owner == owner and branch:
        return branch
    return None


def _failure_payload(
    *,
    code: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
    token_kind: str,
    http_status: int | None,
    reason: str,
    likely_cause: str,
    branch_pushed: bool | None,
    failed_step: str,
    response: httpx.Response | None = None,
) -> dict[str, Any]:
    pushed = "unknown" if branch_pushed is None else "yes" if branch_pushed else "no"
    _record_open_pr_failure_telemetry(
        code=code,
        owner=owner,
        repo=repo,
        head=head,
        base=base,
        token_kind=token_kind,
        http_status=http_status,
        branch_pushed=branch_pushed,
        failed_step=failed_step,
    )
    return {
        "success": False,
        "error": (
            "Failed to open an attributed PR with open_pull_request. "
            f"Reason: {reason}. Likely cause: {likely_cause}. "
            f"Branch pushed: {owner}/{repo}:{head} ({pushed}). PR created: no."
            f"{_github_response_summary(response)}"
        ),
    }


def _record_open_pr_failure_telemetry(
    *,
    code: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
    token_kind: str,
    http_status: int | None,
    branch_pushed: bool | None,
    failed_step: str,
) -> None:
    configurable = _configurable()
    logger.warning(
        "open_pull_request_failed code=%s owner=%s repo=%s head=%s base=%s "
        "http_status=%s token_kind=%s branch_pushed=%s thread_id=%s source=%s",
        code,
        owner,
        repo,
        head,
        base,
        http_status,
        token_kind,
        branch_pushed,
        configurable.get("thread_id"),
        configurable.get("source"),
        extra={
            "open_pull_request_failure": {
                "code": code,
                "owner": owner,
                "repo": repo,
                "head": head,
                "base": base,
                "http_status": http_status,
                "token_kind": token_kind,
                "branch_pushed": branch_pushed,
                "pr_created": False,
                "failed_step": failed_step,
                "thread_id": configurable.get("thread_id"),
                "source": configurable.get("source"),
            }
        },
    )


def _access_failure_payload(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    token_kind: str,
    http_status: int | None,
    reason: str,
    branch_pushed: bool | None,
    failed_step: str,
    response: httpx.Response | None = None,
) -> dict[str, Any]:
    return _failure_payload(
        code=_ACCESS_FAILURE_CODE,
        owner=owner,
        repo=repo,
        head=head,
        base=base,
        token_kind=token_kind,
        http_status=http_status,
        reason=reason,
        likely_cause=(
            "the Open SWE GitHub App or PR author token is not installed on, granted access "
            "to, or able to see this repository or one of the PR branches"
        ),
        branch_pushed=branch_pushed,
        failed_step=failed_step,
        response=response,
    )


def _branch_failure_payload(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    token_kind: str,
    http_status: int,
    branch: str,
    branch_role: str,
    response: httpx.Response | None = None,
) -> dict[str, Any]:
    branch_pushed = False if branch_role == "head" else None
    return _failure_payload(
        code=_BRANCH_FAILURE_CODE,
        owner=owner,
        repo=repo,
        head=head,
        base=base,
        token_kind=token_kind,
        http_status=http_status,
        reason=f"GitHub could not see the {branch_role} branch `{branch}` before PR creation",
        likely_cause=(
            f"the {branch_role} branch does not exist on `{owner}/{repo}` or is not visible "
            "to the PR author token"
        ),
        branch_pushed=branch_pushed,
        failed_step=f"preflight_{branch_role}_branch",
        response=response,
    )


async def _github_get(client: httpx.AsyncClient, token: str, path: str) -> httpx.Response:
    return await client.get(f"{GITHUB_API}{path}", headers=_auth_headers(token))


async def _preflight_pr_access(
    *,
    client: httpx.AsyncClient,
    token: str,
    token_kind: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
) -> dict[str, Any] | None:
    repo_resp = await _github_get(client, token, f"/repos/{owner}/{repo}")
    if repo_resp.status_code in {403, 404}:
        return _access_failure_payload(
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=repo_resp.status_code,
            reason=f"GitHub returned {repo_resp.status_code} while checking repository access",
            branch_pushed=None,
            failed_step="preflight_repo",
            response=repo_resp,
        )
    if repo_resp.status_code != 200:
        return _failure_payload(
            code=_PREFLIGHT_FAILURE_CODE,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=repo_resp.status_code,
            reason=(
                f"GitHub returned {repo_resp.status_code} while checking repository access: "
                f"{_github_message(repo_resp)}"
            ),
            likely_cause="GitHub repository access preflight failed before PR creation",
            branch_pushed=None,
            failed_step="preflight_repo",
            response=repo_resp,
        )

    base_resp = await _github_get(
        client, token, f"/repos/{owner}/{repo}/branches/{quote(base, safe='')}"
    )
    if base_resp.status_code == 404:
        return _branch_failure_payload(
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=base_resp.status_code,
            branch=base,
            branch_role="base",
            response=base_resp,
        )
    if base_resp.status_code in {401, 403}:
        return _access_failure_payload(
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=base_resp.status_code,
            reason=f"GitHub returned {base_resp.status_code} while checking base branch access",
            branch_pushed=None,
            failed_step="preflight_base_branch",
            response=base_resp,
        )
    if base_resp.status_code != 200:
        return _failure_payload(
            code=_PREFLIGHT_FAILURE_CODE,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=base_resp.status_code,
            reason=(
                f"GitHub returned {base_resp.status_code} while checking base branch access: "
                f"{_github_message(base_resp)}"
            ),
            likely_cause="GitHub branch access preflight failed before PR creation",
            branch_pushed=None,
            failed_step="preflight_base_branch",
            response=base_resp,
        )

    head_branch = _head_branch_for_repo(owner, head)
    if head_branch is None:
        return None
    head_resp = await _github_get(
        client, token, f"/repos/{owner}/{repo}/branches/{quote(head_branch, safe='')}"
    )
    if head_resp.status_code == 404:
        return _branch_failure_payload(
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=head_resp.status_code,
            branch=head_branch,
            branch_role="head",
            response=head_resp,
        )
    if head_resp.status_code in {401, 403}:
        return _access_failure_payload(
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=head_resp.status_code,
            reason=f"GitHub returned {head_resp.status_code} while checking head branch access",
            branch_pushed=False,
            failed_step="preflight_head_branch",
            response=head_resp,
        )
    if head_resp.status_code != 200:
        return _failure_payload(
            code=_PREFLIGHT_FAILURE_CODE,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=token_kind,
            http_status=head_resp.status_code,
            reason=(
                f"GitHub returned {head_resp.status_code} while checking head branch access: "
                f"{_github_message(head_resp)}"
            ),
            likely_cause="GitHub branch access preflight failed before PR creation",
            branch_pushed=None,
            failed_step="preflight_head_branch",
            response=head_resp,
        )
    return None


async def _find_existing_pr(
    client: httpx.AsyncClient, token: str, owner: str, repo: str, head: str
) -> dict[str, Any] | None:
    resp = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers=_auth_headers(token),
        params={"head": f"{owner}:{head}", "state": "open"},
    )
    if resp.status_code != 200:
        return None
    items = resp.json()
    return items[0] if isinstance(items, list) and items else None


async def _fetch_pr_details(
    client: httpx.AsyncClient, token: str, owner: str, repo: str, pr_number: int
) -> dict[str, Any]:
    resp = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}",
        headers=_auth_headers(token),
    )
    if resp.status_code != 200:
        logger.debug(
            "GitHub returned %s fetching PR stats for %s/%s#%s: %s",
            resp.status_code,
            owner,
            repo,
            pr_number,
            resp.text,
        )
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _upsert_pull_request(records: object, record: dict[str, Any]) -> list[dict[str, Any]]:
    existing = records if isinstance(records, list) else []
    repo = record.get("repo_full_name")
    number = record.get("number")
    url = record.get("url")
    return [
        item
        for item in existing
        if isinstance(item, dict)
        and not (
            item.get("repo_full_name") == repo
            and item.get("number") == number
            or item.get("url") == url
        )
    ] + [record]


async def _thread_pull_requests(thread_id: str) -> list[dict[str, Any]]:
    try:
        thread = await get_client().threads.get(thread_id)
    except Exception:
        logger.debug("Failed to read existing PR metadata for thread %s", thread_id, exc_info=True)
        return []
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    if not isinstance(metadata, dict):
        return []
    records = metadata.get("pull_requests")
    if isinstance(records, list):
        return [item for item in records if isinstance(item, dict)]
    pr_url = metadata.get("pr_url")
    pr_number = metadata.get("pr_number")
    pr_ref = parse_github_pr_url(pr_url) if isinstance(pr_url, str) else None
    if not pr_ref or not isinstance(pr_number, int) or isinstance(pr_number, bool):
        return []
    return [
        {
            "repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
            "number": pr_number,
            "url": pr_url,
            "title": metadata.get("pr_title") if isinstance(metadata.get("pr_title"), str) else "",
            "state": metadata.get("pr_state")
            if isinstance(metadata.get("pr_state"), str)
            else "open",
            "head_ref": (
                metadata.get("branch_name") if isinstance(metadata.get("branch_name"), str) else ""
            ),
            "base_ref": (
                metadata.get("base_branch")
                if isinstance(metadata.get("base_branch"), str)
                else "main"
            ),
            "author": "",
            "author_avatar_url": "",
            "created_at": "",
            "diff_stats": (
                metadata.get("diff_stats") if isinstance(metadata.get("diff_stats"), dict) else {}
            ),
        }
    ]


async def _record_pr_telemetry(
    *,
    client: httpx.AsyncClient,
    token: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
    pr: dict[str, Any],
) -> None:
    pr_number = pr.get("number")
    if not isinstance(pr_number, int):
        return
    try:
        details = await _fetch_pr_details(client, token, owner, repo, pr_number)
        config = get_config()
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        thread_id = configurable.get("thread_id")
        github_login = configurable.get("github_login")
        user_email = configurable.get("user_email")
        if not isinstance(github_login, str) or not github_login.strip():
            from ..dashboard.user_mappings import login_for_email

            github_login = (
                await login_for_email(user_email if isinstance(user_email, str) else None) or ""
            )
        pr_url = details.get("html_url") or pr.get("html_url")
        merged = bool(details.get("merged"))
        is_draft = bool(details.get("draft", pr.get("draft")))
        state = details.get("state") if isinstance(details.get("state"), str) else "open"
        additions_value = details.get("additions")
        additions = additions_value if isinstance(additions_value, int) else 0
        deletions_value = details.get("deletions")
        deletions = deletions_value if isinstance(deletions_value, int) else 0
        changed_files_value = details.get("changed_files")
        changed_files = changed_files_value if isinstance(changed_files_value, int) else 0
        await record_agent_pr_usage(
            thread_id=thread_id if isinstance(thread_id, str) else None,
            github_login=github_login,
            user_email=user_email if isinstance(user_email, str) else None,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url if isinstance(pr_url, str) else None,
            head=head,
            base=base,
            additions=additions,
            deletions=deletions,
            changed_files=changed_files,
            state=state,
            merged=merged,
            created_at=details.get("created_at") or pr.get("created_at"),
            merged_at=details.get("merged_at") or pr.get("merged_at"),
        )
        if isinstance(thread_id, str) and thread_id:
            repo_private = None
            base_repo = details.get("base", {}).get("repo")
            if isinstance(base_repo, dict) and isinstance(base_repo.get("private"), bool):
                repo_private = base_repo["private"]
            pr_state = derive_pr_state(state=state, merged=merged, draft=is_draft)
            pr_title = details.get("title") or pr.get("title")
            pr_user = details.get("user") or pr.get("user")
            author = pr_user.get("login") if isinstance(pr_user, dict) else None
            author_avatar_url = pr_user.get("avatar_url") if isinstance(pr_user, dict) else None
            diff_stats = {
                "files": changed_files,
                "additions": additions,
                "deletions": deletions,
            }
            record = {
                "repo_full_name": f"{owner}/{repo}",
                "number": pr_number,
                "url": pr_url if isinstance(pr_url, str) else "",
                "title": pr_title if isinstance(pr_title, str) else "",
                "state": pr_state,
                "head_ref": head,
                "base_ref": base,
                "author": author if isinstance(author, str) else "",
                "author_avatar_url": (
                    author_avatar_url if isinstance(author_avatar_url, str) else ""
                ),
                "created_at": (
                    details.get("created_at")
                    if isinstance(details.get("created_at"), str)
                    else pr.get("created_at")
                    if isinstance(pr.get("created_at"), str)
                    else ""
                ),
                "diff_stats": diff_stats,
            }
            pull_requests = _upsert_pull_request(await _thread_pull_requests(thread_id), record)
            metadata: dict[str, Any] = {
                "agent_kind": "agent",
                "pr_url": pr_url if isinstance(pr_url, str) else "",
                "pr_number": pr_number,
                "pr_state": pr_state,
                "pr_title": pr_title,
                "branch_name": head,
                "base_branch": base,
                "diff_stats": diff_stats,
                "pull_requests": pull_requests,
                "pr_urls": [
                    item["url"]
                    for item in pull_requests
                    if isinstance(item.get("url"), str) and item["url"]
                ],
            }
            if repo_private is not None:
                metadata["repo_private"] = repo_private
            await get_client().threads.update(thread_id=thread_id, metadata=metadata)
            slack_thread = configurable.get("slack_thread")
            active = await get_active_slack_thread(
                get_client(),
                thread_id,
                slack_thread if isinstance(slack_thread, dict) else None,
            )
            if active and is_code_channel_session(str(active.get("thread_ts") or "")):
                channel_id = str(active.get("channel_id") or "")
                await set_context_bar(
                    channel_id,
                    repo_context_bar_items(
                        {"owner": owner, "name": repo},
                        branch=head,
                        pr_url=pr_url if isinstance(pr_url, str) else "",
                    ),
                )
                response = await client.get(
                    f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={**_auth_headers(token), "Accept": "application/vnd.github.v3.diff"},
                )
                if response.status_code == 200 and response.text.strip():
                    await set_view(
                        channel_id,
                        "diff",
                        content=response.text,
                        base_branch=base,
                        head_branch=head,
                    )
    except Exception:
        logger.debug(
            "Failed to record PR usage for %s/%s#%s", owner, repo, pr_number, exc_info=True
        )


async def _plan_reference_line(configurable: dict[str, Any]) -> str | None:
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str):
        return None
    try:
        plan = await get_plan_content(thread_id)
    except Exception:
        logger.debug("Failed to look up plan content for %s", thread_id, exc_info=True)
        return None
    if not plan or not str(plan.get("html") or plan.get("markdown") or "").strip():
        return None
    plan_url = dashboard_plan_url(thread_id)
    if not plan_url:
        return None
    return f"- Plan: {plan_url}"


async def _build_source_reference_lines(configurable: dict[str, Any]) -> list[str]:
    """Build source reference lines for the run."""
    source = configurable.get("source")
    lines: list[str] = []

    if source == "slack":
        slack_thread = configurable.get("slack_thread") or {}
        thread_id = configurable.get("thread_id")
        active = await get_active_slack_thread(
            get_client(),
            thread_id if isinstance(thread_id, str) else None,
            slack_thread if isinstance(slack_thread, dict) else None,
        )
        slack_thread = active or {}
        channel_id = slack_thread.get("channel_id")
        thread_ts = slack_thread.get("thread_ts")
        permalink = slack_thread.get("permalink")
        if not isinstance(permalink, str) or not permalink.strip():
            permalink = None
            if channel_id and thread_ts:
                permalink = await get_slack_permalink(channel_id, thread_ts)
        if isinstance(permalink, str) and permalink.strip():
            lines.append(f"- Slack thread: {permalink.strip()}")
    elif source == "linear":
        linear_issue = configurable.get("linear_issue") or {}
        url = linear_issue.get("url")
        identifier = linear_issue.get("identifier")
        if url:
            lines.append(f"- Linear ticket: [{identifier or url}]({url})")
        elif identifier:
            lines.append(f"- Linear ticket: {identifier}")
    elif source in ("github", "github_issue"):
        github_issue = configurable.get("github_issue") or {}
        url = github_issue.get("url")
        number = github_issue.get("number")
        if url:
            label = f"#{number}" if number else url
            lines.append(f"- GitHub issue: [{label}]({url})")
        elif number:
            lines.append(f"- GitHub issue: #{number}")

    return lines


async def _is_private_repo(client: httpx.AsyncClient, token: str, owner: str, repo: str) -> bool:
    """Return True only when GitHub confirms the repo is private."""
    resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=_auth_headers(token))
    if resp.status_code != 200:  # noqa: PLR2004
        return False
    data = resp.json()
    return bool(data.get("private")) if isinstance(data, dict) else False


async def _maybe_append_references(
    client: httpx.AsyncClient, token: str, owner: str, repo: str, body: str
) -> str:
    """Append run references to the PR body."""
    try:
        if _REFERENCES_HEADING in body:
            return body
        configurable = get_config().get("configurable", {})
        if not isinstance(configurable, dict):
            configurable = {}
        lines: list[str] = []
        plan_line = await _plan_reference_line(configurable)
        if plan_line:
            lines.append(plan_line)
        try:
            source_lines = await _build_source_reference_lines(configurable)
            if source_lines and await _is_private_repo(client, token, owner, repo):
                lines.extend(source_lines)
        except Exception:
            logger.debug("Failed to append source references to PR body", exc_info=True)
        if not lines:
            return body
        return f"{body.rstrip()}\n\n{_REFERENCES_HEADING}\n" + "\n".join(lines)
    except Exception:
        logger.debug("Failed to append references to PR body", exc_info=True)
        return body


async def _open_pull_request(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool,
) -> dict[str, Any]:
    token, kind = await _resolve_pr_author_token()
    if not token:
        return _failure_payload(
            code="no_github_token",
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=kind,
            http_status=None,
            reason="No GitHub token was available to open the pull request",
            likely_cause="the triggering user is not authorized and no GitHub App token is available",
            branch_pushed=None,
            failed_step="resolve_pr_author_token",
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        preflight_failure = await _preflight_pr_access(
            client=client,
            token=token,
            token_kind=kind,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
        )
        if preflight_failure is not None:
            return preflight_failure
        body = await _maybe_append_references(client, token, owner, repo, body)
        draft = _effective_draft(draft)
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=_auth_headers(token),
            json=payload,
        )
        if resp.status_code == 201:
            pr = resp.json()
            if isinstance(pr, dict):
                await _record_pr_telemetry(
                    client=client,
                    token=token,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    pr=pr,
                )
            return {
                "success": True,
                "created": True,
                "url": pr.get("html_url"),
                "number": pr.get("number"),
                "author": (pr.get("user") or {}).get("login"),
                "token_kind": kind,
            }

        # A PR for this head branch may already exist — return it so the agent
        # switches to `gh pr edit` for updates instead of erroring out.
        if resp.status_code == 422:  # noqa: PLR2004
            existing = await _find_existing_pr(client, token, owner, repo, head)
            if existing is not None:
                await _record_pr_telemetry(
                    client=client,
                    token=token,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    pr=existing,
                )
                return {
                    "success": True,
                    "created": False,
                    "url": existing.get("html_url"),
                    "number": existing.get("number"),
                    "author": (existing.get("user") or {}).get("login"),
                    "token_kind": kind,
                }

        if resp.status_code == 404:
            return _access_failure_payload(
                owner=owner,
                repo=repo,
                head=head,
                base=base,
                token_kind=kind,
                http_status=resp.status_code,
                reason="GitHub returned 404 while creating the pull request",
                branch_pushed=True,
                failed_step="create_pull_request",
                response=resp,
            )

        return _failure_payload(
            code="github_pr_create_failed",
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            token_kind=kind,
            http_status=resp.status_code,
            reason=f"GitHub returned {resp.status_code} while creating the pull request: {_github_message(resp)}",
            likely_cause="GitHub rejected the pull request creation request",
            branch_pushed=True,
            failed_step="create_pull_request",
            response=resp,
        )


async def open_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool = True,
) -> dict[str, Any]:
    """Open a draft GitHub pull request attributed to the triggering user.

    Use this to OPEN a NEW pull request (instead of `gh pr create`) so the PR is
    created as the person who triggered the run rather than open-swe[bot]. Push
    your branch with `git push origin <branch>` BEFORE calling this.

    For everything else — updating an existing PR, marking it ready for review,
    commenting, reading status — keep using `gh`. If a PR already
    exists for the branch, this returns that PR's URL without creating a
    duplicate; switch to `gh pr edit` for updates.

    Args:
        owner: Repository owner/org (e.g. "langchain-ai").
        repo: Repository name (e.g. "open-swe").
        head: The branch with your changes (already pushed to origin).
        base: The branch you want to merge into (e.g. "main").
        title: PR title.
        body: PR description (Markdown).
        draft: Requested draft status. The authenticated user's dashboard preference
          overrides this value for newly created PRs; existing PRs are returned unchanged.

    Returns:
        On success: {"success": True, "created": bool, "url": str, "number": int,
        "author": str}. ``created`` is False when an open PR already existed.
        On failure: {"success": False, "error": str}, where ``error`` states what
        failed and quotes the request, status, headers, and body GitHub actually
        returned — read it and decide what to do next.
    """
    return await _open_pull_request(
        owner=owner,
        repo=repo,
        head=head,
        base=base,
        title=title,
        body=body,
        draft=draft,
    )
