from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from ..reviewer_findings import list_findings
from .langsmith import create_langsmith_feedback, delete_langsmith_feedback
from .reviewer_outcomes import outcome_from_score, upsert_finding_outcome

logger = logging.getLogger(__name__)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

GITHUB_FEEDBACK_REACTIONS: dict[str, float] = {
    "+1": 1.0,
    "-1": 0.0,
}

_REACTION_STATE_NAMESPACE = "github_reaction_state"
_REACTION_EVENT_NAMESPACE = "github_reaction_events"
_PULL_URL_RE = re.compile(r"/pulls/(\d+)\Z")


def _reviewer_thread_id(owner: str, repo: str, pr_number: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{owner}/{repo}/pr/{pr_number}/reviewer"))


def _read_active_reactions(item: dict[str, Any] | None) -> set[str]:
    if not item:
        return set()
    value = item.get("value")
    if not isinstance(value, dict):
        return set()
    reactions = value.get("reactions")
    if not isinstance(reactions, list):
        return set()
    return {reaction for reaction in reactions if isinstance(reaction, str)}


def _reaction_state_key(run_id: str, user_login: str, comment_id: int) -> str:
    return f"{run_id}:{user_login}:{comment_id}"


def _feedback_key(owner: str, repo: str, user_login: str, comment_id: int) -> str:
    return f"github_reaction:{owner}/{repo}:{user_login}:{comment_id}"


def _score_reactions(reactions: set[str]) -> float | None:
    scores = {
        GITHUB_FEEDBACK_REACTIONS[reaction]
        for reaction in reactions
        if reaction in GITHUB_FEEDBACK_REACTIONS
    }
    if len(scores) != 1:
        return None
    return next(iter(scores))


def _extract_pr_number(payload: dict[str, Any]) -> int | None:
    pull_request = payload.get("pull_request")
    if isinstance(pull_request, dict) and isinstance(pull_request.get("number"), int):
        return pull_request["number"]

    comment = payload.get("comment")
    if isinstance(comment, dict):
        url = comment.get("pull_request_url")
        if isinstance(url, str):
            match = _PULL_URL_RE.search(url)
            if match:
                return int(match.group(1))
    return None


async def _event_was_processed(
    langgraph_client: LangGraphClient, repo_key: str, event_id: str
) -> bool:
    if not event_id:
        return False
    item = await langgraph_client.store.get_item((_REACTION_EVENT_NAMESPACE, repo_key), event_id)
    return bool(item)


async def _mark_event_processed(
    langgraph_client: LangGraphClient, repo_key: str, event_id: str
) -> None:
    if not event_id:
        return
    await langgraph_client.store.put_item(
        (_REACTION_EVENT_NAMESPACE, repo_key), event_id, {"event_id": event_id}
    )


async def _update_reaction_state(
    langgraph_client: LangGraphClient,
    *,
    repo_key: str,
    run_id: str,
    user_login: str,
    comment_id: int,
    reaction: str,
    added: bool,
) -> set[str]:
    namespace = (_REACTION_STATE_NAMESPACE, repo_key)
    key = _reaction_state_key(run_id, user_login, comment_id)
    item = await langgraph_client.store.get_item(namespace, key)
    active_reactions = _read_active_reactions(item)
    if added:
        active_reactions.add(reaction)
    else:
        active_reactions.discard(reaction)
    if not active_reactions:
        await langgraph_client.store.delete_item(namespace, key)
        return active_reactions
    await langgraph_client.store.put_item(
        namespace,
        key,
        {
            "run_id": run_id,
            "user_login": user_login,
            "comment_id": comment_id,
            "reactions": sorted(active_reactions),
        },
    )
    return active_reactions


async def process_github_reaction(
    payload: dict[str, Any],
    *,
    delivery_id: str = "",
    added: bool,
) -> None:
    reaction = payload.get("reaction")
    content = reaction.get("content") if isinstance(reaction, dict) else None
    if not isinstance(content, str) or content not in GITHUB_FEEDBACK_REACTIONS:
        return

    comment = payload.get("comment")
    comment_id = comment.get("id") if isinstance(comment, dict) else None
    if not isinstance(comment_id, int):
        return

    repo = payload.get("repository")
    owner = repo.get("owner", {}).get("login") if isinstance(repo, dict) else None
    repo_name = repo.get("name") if isinstance(repo, dict) else None
    pr_number = _extract_pr_number(payload)
    sender = payload.get("sender")
    user_login = sender.get("login") if isinstance(sender, dict) else None
    if not (
        isinstance(owner, str)
        and owner
        and isinstance(repo_name, str)
        and repo_name
        and isinstance(pr_number, int)
        and isinstance(user_login, str)
        and user_login
    ):
        return

    langgraph_client = get_client(url=LANGGRAPH_URL)
    repo_key = f"{owner}/{repo_name}"
    if await _event_was_processed(langgraph_client, repo_key, delivery_id):
        return

    thread_id = _reviewer_thread_id(owner, repo_name, pr_number)
    findings = await list_findings(thread_id)
    finding = next(
        (
            candidate
            for candidate in findings
            if candidate.get("github_review_comment_id") == comment_id
        ),
        None,
    )
    if finding is None:
        logger.debug("No tracked finding for GitHub review comment id %s", comment_id)
        return

    run_id = finding.get("github_review_run_id")
    if not isinstance(run_id, str) or not run_id:
        logger.debug("Finding %s has no LangSmith run id for feedback", finding.get("id"))
        return

    active_reactions = await _update_reaction_state(
        langgraph_client,
        repo_key=repo_key,
        run_id=run_id,
        user_login=user_login,
        comment_id=comment_id,
        reaction=content,
        added=added,
    )

    key = _feedback_key(owner, repo_name, user_login, comment_id)
    source_info = {
        "source": "github_review_reaction",
        "owner": owner,
        "repo": repo_name,
        "pr_number": pr_number,
        "comment_id": comment_id,
        "finding_id": finding.get("id"),
        "user_login": user_login,
    }
    score = _score_reactions(active_reactions)
    if score is None:
        success = await asyncio.to_thread(delete_langsmith_feedback, run_id, key)
    else:
        success = await asyncio.to_thread(
            create_langsmith_feedback,
            run_id,
            key,
            score=score,
            comment=f"GitHub review reaction feedback from {user_login}",
            source_info={**source_info, "reactions": sorted(active_reactions)},
        )
    outcome = outcome_from_score(score, source="github")
    if outcome is not None:
        label, label_source = outcome
        await asyncio.to_thread(
            upsert_finding_outcome,
            finding,
            label=label,
            label_source=label_source,
            repo=repo_key,
            pr_number=pr_number,
            pr_url=f"https://github.com/{repo_key}/pull/{pr_number}",
            head_sha=str(finding.get("first_seen_sha") or ""),
            run_id=run_id,
            thread_id=thread_id,
        )

    if success:
        await _mark_event_processed(langgraph_client, repo_key, delivery_id)


async def process_github_reaction_added(payload: dict[str, Any], delivery_id: str = "") -> None:
    await process_github_reaction(payload, delivery_id=delivery_id, added=True)


async def process_github_reaction_removed(payload: dict[str, Any], delivery_id: str = "") -> None:
    await process_github_reaction(payload, delivery_id=delivery_id, added=False)
