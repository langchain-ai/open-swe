"""Start PR repairs in an accessible coding thread."""

import uuid

from fastapi import HTTPException

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.dashboard.threads.access import _ensure_dashboard_github_token
from agent.dashboard.threads.runs import (
    _build_dashboard_configurable,
    _create_dashboard_thread_record,
)
from agent.dashboard.threads.summary import _assert_thread_postable
from agent.dispatch import dispatch_agent_run
from agent.github.pull_request_status import pull_request_identity
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_pr_state import agent_thread_pr_state_lock


async def fix_pull_request(
    owner: str, repo: str, number: int, login: str, email: str | None = None
) -> dict[str, str]:
    full_name = f"{owner}/{repo}"
    if pull_request_identity({"repo_full_name": full_name, "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    await require_repo_access_for_user(login, full_name)
    await _ensure_dashboard_github_token(login)
    url = f"https://github.com/{full_name}/pull/{number}"
    client = langgraph_client()
    prompt = (
        f"Fix merge conflicts and failing CI checks on {url}. Inspect the current PR and checks, "
        "work on its existing head branch, run relevant validation, and push the fixes to that PR. "
        "Do not merge or close the PR."
    )
    async with agent_thread_pr_state_lock(client, f"fix:{login}:{url}"):
        candidates = {}
        for query in ({"pr_url": url}, {"pr_urls": [url]}):
            offset = 0
            while True:
                page = await client.threads.search(metadata=query, limit=50, offset=offset)
                for thread in page:
                    metadata = thread_metadata(thread)
                    if metadata.get("kind") or metadata.get("graph_id") not in (None, "agent"):
                        continue
                    try:
                        _assert_thread_postable(metadata, login, email)
                    except HTTPException:
                        continue
                    candidates[thread["thread_id"]] = thread
                if len(page) < 50:
                    break
                offset += 50
        if candidates:
            thread = max(candidates.values(), key=lambda item: str(item.get("updated_at", "")))
        else:
            thread = await _create_dashboard_thread_record(
                str(uuid.uuid4()),
                login=login,
                email=email,
                repo_config={"owner": owner, "name": repo},
                prompt=prompt,
                title=f"Fix {full_name}#{number}",
            )
            await client.threads.update(
                thread_id=thread["thread_id"], metadata={"pr_url": url, "pr_number": number}
            )
        thread_id = thread["thread_id"]
        async with agent_thread_pr_state_lock(client, thread_id):
            await client.threads.update(
                thread_id=thread_id,
                metadata={"resolved": False, "resolved_at_ms": None, "auto_resolved_by_prs": False},
            )
        current = await client.threads.get(thread_id)
        _assert_thread_postable(thread_metadata(current), login, email)
        configurable = await _build_dashboard_configurable(
            thread_id, login, thread_metadata(current)
        )
        await dispatch_agent_run(
            thread_id,
            prompt,
            configurable,
            source="dashboard",
            client=client,
            multitask_strategy="enqueue",
        )
        return {"thread_id": thread_id}
