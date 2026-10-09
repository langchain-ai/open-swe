"""Connect the review page to the pull request's main agent thread."""

import json
from collections.abc import AsyncIterator
from typing import TypedDict

from fastapi import HTTPException

from openswe.threads import pr_fixes
from openswe.threads.handlers import get_web_thread_state
from openswe.threads.proxy import (
    proxy_web_thread_commands,
    proxy_web_thread_history,
    proxy_web_thread_stream_events,
    require_json_content_type,
)


class ReviewChat(TypedDict):
    available: bool
    assistant_id: str
    thread_id: str


async def get_review_chat(
    owner: str, repo: str, pr_number: int, login: str, email: str | None = None
) -> ReviewChat:
    thread = await pr_fixes.start_pull_request_thread(
        owner,
        repo,
        pr_number,
        login,
        email,
        intent=pr_fixes.OpenThreadIntent(
            intent="open", title=f"Discuss {owner}/{repo}#{pr_number}"
        ),
    )
    return {"available": True, "assistant_id": "agent", "thread_id": thread.thread_id}


async def assert_chat_thread_access(
    thread_id: str, owner: str, repo: str, pr_number: int, login: str
) -> None:
    threads = await pr_fixes.find_pr_threads(owner, repo, pr_number, login, None)
    if not any(thread["thread_id"] == thread_id for thread in threads):
        raise HTTPException(404, "chat not found")


async def proxy_review_chat_commands(
    owner: str,
    repo: str,
    pr_number: int,
    login: str,
    thread_id: str,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    require_json_content_type(content_type)
    try:
        command = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "command body must be a JSON object") from exc
    if not isinstance(command, dict):
        raise HTTPException(400, "command body must be a JSON object")
    return await proxy_web_thread_commands(
        thread_id, login, json.dumps(command).encode(), content_type=content_type
    )


async def proxy_review_chat_stream_events(
    owner: str,
    repo: str,
    pr_number: int,
    login: str,
    thread_id: str,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> AsyncIterator[bytes]:
    await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    return await proxy_web_thread_stream_events(thread_id, login, body, content_type=content_type)


async def proxy_review_chat_state(
    owner: str, repo: str, pr_number: int, login: str, thread_id: str
) -> tuple[int, bytes, str | None]:
    await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    state = await get_web_thread_state(thread_id, login)
    return 200, json.dumps(state).encode(), "application/json"


async def proxy_review_chat_history(
    owner: str,
    repo: str,
    pr_number: int,
    login: str,
    thread_id: str,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    return await proxy_web_thread_history(thread_id, login, body, content_type=content_type)
