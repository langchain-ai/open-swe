"""Target function for the reviewer eval.

Spawns the reviewer graph over `langgraph_sdk` for one PR, waits for
completion, and returns every `github_comment` tool call the agent made as
the structured output for the eval.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from langgraph_sdk import get_client

REVIEWER_ASSISTANT_ID = os.getenv("REVIEWER_ASSISTANT_ID", "reviewer")
LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://localhost:2024")

_THREAD_IDS: set[str] = set()
_THREAD_IDS_LOCK = threading.Lock()


def _record_thread_id(thread_id: str) -> None:
    with _THREAD_IDS_LOCK:
        _THREAD_IDS.add(thread_id)


def drain_thread_ids() -> set[str]:
    """Return and clear thread IDs created by ``review_pr`` so far.

    Used by ``run_eval`` to delete threads after the experiment finishes.
    Underlying provider sandboxes time out via their own TTL — deleting the
    LangGraph thread frees the checkpoint/metadata records, not the sandbox.
    """
    with _THREAD_IDS_LOCK:
        snapshot = set(_THREAD_IDS)
        _THREAD_IDS.clear()
    return snapshot


def _build_user_message(inputs: dict[str, Any]) -> str:
    return (
        f"Review pull request {inputs['pr_url']}.\n\n"
        f"- repo: {inputs['repo']}\n"
        f"- pr_number: {inputs['pr_number']}\n"
        f"- title: {inputs.get('pr_title', '')}\n"
        f"- base_sha: {inputs['base_sha']}\n"
        f"- head_sha: {inputs['head_sha']}\n"
        f"- base_ref: {inputs.get('base_ref', '')}\n"
        f"- head_ref: {inputs.get('head_ref', '')}\n\n"
        f"Clone the repo, check out the base SHA, fetch the PR head, and review "
        f"the diff. Record each issue you find with the `github_comment` tool."
    )


async def review_pr(inputs: dict[str, Any]) -> dict[str, Any]:
    """LangSmith target: run the reviewer agent on one PR."""
    client = get_client(url=LANGGRAPH_URL)
    thread = await client.threads.create()
    thread_id: str = thread["thread_id"]
    _record_thread_id(thread_id)
    result = await client.runs.wait(
        thread_id,
        assistant_id=REVIEWER_ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": _build_user_message(inputs)}]},
        config={"configurable": {"__is_for_execution__": True}},
    )
    return {"comments": _extract_comments(result)}


def _extract_comments(result: Any) -> list[dict[str, Any]]:
    """Collect every `github_comment` tool call from the run's message stream."""
    if not isinstance(result, dict):
        return []
    comments: list[dict[str, Any]] = []
    for msg in result.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if tc.get("name") != "github_comment":
                continue
            args = tc.get("args") or {}
            if {"file", "line", "body", "severity"} <= args.keys():
                comments.append(
                    {
                        "file": args["file"],
                        "line": args["line"],
                        "body": args["body"],
                        "severity": args["severity"],
                    }
                )
    return comments
