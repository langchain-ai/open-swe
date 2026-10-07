"""Privacy-preserving previews of links to this deployment's dashboard."""

import logging
from dataclasses import dataclass
from typing import Literal, TypedDict
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel

from openswe.config import ENV
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GITHUB_GRAPHQL, github_client, github_request
from openswe.github.pull_request_status import pull_request_identity
from openswe.review.findings import REVIEWER_THREAD_KIND
from openswe.slack.blocks import Block, actions, button, context, escape, plain_text
from openswe.slack.channels import SlackChannel
from openswe.slack.dm import note_for_concierge
from openswe.slack.events import claim_slack_event
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error, slack_identity
from openswe.slack.payloads import SlackEventEnvelope
from openswe.threads.summary import metadata_title, thread_is_readable
from openswe.users import User, is_authorized_github_login
from openswe.utils.dashboard_links import dashboard_base_url, dashboard_thread_id
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_PR_QUERY = """
query DashboardUnfurl($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      title state isDraft author { login } reviewDecision
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    }
  }
}
"""


@dataclass(frozen=True)
class DashboardLink:
    url: str
    thread_id: str | None = None
    pr: tuple[str, str, int] | None = None


class UnfurlAttachment(TypedDict):
    fallback: str
    blocks: list[Block]


class _Author(BaseModel):
    login: str


class _PullRequest(BaseModel):
    title: str
    state: Literal["OPEN", "CLOSED", "MERGED"]
    isDraft: bool
    author: _Author | None = None
    reviewDecision: Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"] | None = None


def dashboard_link(url: str) -> DashboardLink | None:
    """Accept only actual detail routes on the configured dashboard origin."""
    try:
        parsed, base = urlsplit(url), urlsplit(dashboard_base_url())
        if (
            not base.netloc
            or parsed.scheme not in {"http", "https"}
            or (parsed.scheme, parsed.netloc.lower()) != (base.scheme, base.netloc.lower())
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        path = parsed.path.removeprefix(base.path.rstrip("/"))
        if not path.startswith("/"):
            return None
        if thread_id := dashboard_thread_id(url):
            UUID(thread_id)
            return DashboardLink(url, thread_id=thread_id)
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["agents", "reviews"]:
            owner, repo, number = parts[2:]
        elif len(parts) == 4 and parts[2] == "pull":
            owner, repo, _, number = parts
        else:
            return None
        if not number.isascii() or not number.isdigit():
            return None
        pr = pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": int(number)})
        return DashboardLink(url, pr=pr) if pr else None
    except ValueError:
        logger.debug("Invalid dashboard unfurl URL")
        return None


def preview_card(
    link: DashboardLink, title: str | None = None, status: str | None = None
) -> UnfurlAttachment:
    label = "Open SWE thread" if link.thread_id else "Open SWE pull request review"
    title = (title or label)[:250]
    return {
        "fallback": f"{label}: {title} — {status or 'Sign in to view details'}",
        "blocks": [
            {"type": "section", "text": plain_text(title)},
            context(
                escape(
                    status or "Sign in to Open SWE to view details. Access is checked on opening."
                )
            ),
            actions(button("Open", action_id="open_swe_dashboard_unfurl", url=link.url)),
        ],
    }


async def _details(link: DashboardLink, user: User) -> UnfurlAttachment:
    if link.thread_id:
        thread = await langgraph_client().threads.get(link.thread_id)
        metadata = thread_metadata(thread)
        if thread_is_readable(metadata, user.github_login, user.email):
            if (
                metadata.get("kind") == REVIEWER_THREAD_KIND
                or metadata.get("source") == "review_chat"
            ):
                raw_pr = metadata.get("pr")
                owner = (
                    raw_pr.get("owner") if isinstance(raw_pr, dict) else metadata.get("repo_owner")
                )
                repo = raw_pr.get("name") if isinstance(raw_pr, dict) else metadata.get("repo_name")
                identity = pull_request_identity(
                    {
                        "repo_full_name": f"{owner}/{repo}",
                        "number": raw_pr.get("number")
                        if isinstance(raw_pr, dict)
                        else metadata.get("pr_number"),
                    }
                )
                if identity is None:
                    return preview_card(link)
                await require_repo_access_for_user(
                    user.github_login, f"{identity[0]}/{identity[1]}"
                )
            status = (
                "Resolved"
                if metadata.get("resolved") is True
                else {
                    "busy": "Running",
                    "idle": "Idle",
                    "interrupted": "Interrupted",
                    "error": "Error",
                }.get(thread.get("status"), "Status unavailable")
            )
            return preview_card(link, metadata_title(metadata), status)
    elif link.pr:
        owner, repo, number = link.pr
        token = await require_repo_access_for_user(user.github_login, f"{owner}/{repo}")
        async with github_client(token=token) as client:
            response = await github_request(
                client,
                "POST",
                GITHUB_GRAPHQL,
                json={
                    "query": _PR_QUERY,
                    "variables": {"owner": owner, "repo": repo, "number": number},
                },
            )
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ValueError("Unavailable pull request preview")
        data = payload.get("data")
        repository = data.get("repository") if isinstance(data, dict) else None
        raw = repository.get("pullRequest") if isinstance(repository, dict) else None
        pr = _PullRequest.model_validate(raw)
        status = ["Draft" if pr.isDraft and pr.state == "OPEN" else pr.state.capitalize()]
        if pr.author:
            status.append(f"Author: {pr.author.login}")
        if pr.reviewDecision:
            status.append(f"Review: {pr.reviewDecision.replace('_', ' ').lower()}")
        commits = raw.get("commits") if isinstance(raw, dict) else None
        nodes = commits.get("nodes") if isinstance(commits, dict) else None
        node = nodes[0] if isinstance(nodes, list) and len(nodes) == 1 else None
        commit = node.get("commit") if isinstance(node, dict) else None
        rollup = commit.get("statusCheckRollup") if isinstance(commit, dict) else None
        state = rollup.get("state") if isinstance(rollup, dict) else None
        if state in {"SUCCESS", "FAILURE", "PENDING", "ERROR", "EXPECTED"}:
            status.append(f"Check rollup: {state.lower()}")
        return preview_card(link, pr.title, " · ".join(status))
    return preview_card(link)


async def unfurl_dashboard_links(envelope: SlackEventEnvelope) -> None:
    """Unfurl posted links; shared destinations never receive resource details."""
    event = envelope.event
    if event is None:
        return
    links = [link for item in event.links[:5] if (link := dashboard_link(item.url))]
    channel_id, user_id = event.resolve_channel_id(), event.resolve_user_id()
    if not links or not channel_id or not event.message_ts or channel_id == "COMPOSER":
        return
    try:
        async with SlackClient.bot() as client:
            identity = await slack_identity(client)
            if envelope.team_id != identity["team_id"] or (
                ENV.SLACK_APP_ID.optional() and envelope.api_app_id != ENV.SLACK_APP_ID.get()
            ):
                return
            channel = await SlackChannel.load(channel_id, use_cache=False)
            if channel is None or not channel.context.allows_operations:
                return
            if not await claim_slack_event(envelope.event_id):
                return
            user = None
            if channel.details.is_im is True and channel.payload.get("user") == user_id:
                try:
                    linked = await User.for_identity("slack", user_id)
                    if (
                        linked
                        and linked.github_login
                        and any(
                            item.provider == "slack"
                            and item.external_id == user_id
                            and item.team_id == envelope.team_id
                            for item in linked.identities
                        )
                        and await is_authorized_github_login(linked.github_login)
                    ):
                        user = linked
                except Exception as exc:
                    logger.warning(
                        "Dashboard unfurl identity unavailable",
                        extra={"error_type": type(exc).__name__},
                    )
            unfurls: dict[str, UnfurlAttachment] = {}
            for link in links:
                card = preview_card(link)
                if user is not None:
                    try:
                        card = await _details(link, user)
                    except Exception as exc:
                        logger.warning(
                            "Dashboard unfurl details unavailable",
                            extra={"error_type": type(exc).__name__},
                        )
                unfurls[link.url] = card
            await client.chat_unfurl(
                channel=channel_id,
                ts=event.message_ts,
                unfurls={url: dict(card) for url, card in unfurls.items()},
            )
        if channel.details.is_im is True and channel.payload.get("user") == user_id:
            await note_for_concierge(
                user_id,
                channel_id,
                "Open SWE added dashboard link previews: "
                + "\n".join(card["fallback"] for card in unfurls.values()),
            )
    except SLACK_REQUEST_ERRORS as exc:
        logger.warning("Dashboard unfurl failed", extra={"slack_error": slack_error(exc)})
    except Exception as exc:
        logger.warning("Dashboard unfurl unavailable", extra={"error_type": type(exc).__name__})
