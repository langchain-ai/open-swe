"""Target function for the reviewer eval.

Invokes the Open SWE Reviewer graph over the langgraph_sdk client and returns
the structured comments produced by the agent's `submit_review` tool call.

The reviewer graph itself is not part of this PR — wire `REVIEWER_ASSISTANT_ID`
to whatever graph id you want to evaluate once it exists.
"""

from __future__ import annotations

import os
from typing import Any

from langgraph_sdk import get_client

REVIEWER_ASSISTANT_ID = os.getenv("REVIEWER_ASSISTANT_ID", "reviewer")
LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://localhost:2024")


async def review_pr(inputs: dict[str, Any]) -> dict[str, Any]:
    """LangSmith target: run the reviewer agent on one PR.

    `inputs` carries: repo, pr_number, pr_url, base_sha, head_sha, base_ref,
    head_ref, pr_title. The reviewer graph is responsible for cloning the
    repo at base_sha, fetching the PR's head, and emitting structured review
    comments via a `submit_review` tool whose args become the graph output.

    Returns: {"comments": [{file, line, severity, body}, ...]}.
    """
    client = get_client(url=LANGGRAPH_URL)
    thread = await client.threads.create()
    result = await client.runs.wait(
        thread["thread_id"],
        assistant_id=REVIEWER_ASSISTANT_ID,
        input={"pr": inputs},
    )
    return {"comments": _extract_comments(result)}


def _extract_comments(result: Any) -> list[dict]:
    """Pull the submit_review payload out of the graph's final state.

    Supports two shapes:
      1. Graph state contains a top-level `review` field populated by the tool
         (preferred — wire the reviewer graph to set this).
      2. Last AI message includes a `submit_review` tool call; we parse args.
    """
    if isinstance(result, dict):
        if isinstance(result.get("review"), dict) and "comments" in result["review"]:
            return list(result["review"]["comments"])
        if isinstance(result.get("comments"), list):
            return list(result["comments"])

        messages = result.get("messages") or []
        for msg in reversed(messages):
            tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else None
            for tc in tool_calls or []:
                if tc.get("name") == "submit_review":
                    args = tc.get("args") or {}
                    if isinstance(args.get("comments"), list):
                        return list(args["comments"])
    return []
