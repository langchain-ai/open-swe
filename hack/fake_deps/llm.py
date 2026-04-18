"""Scripted-response LLM proxy for Anthropic Messages API.

Routes under ``/anthropic``. The only way this proxy returns a response is if
a test has *already* told it what to say — there is no upstream, no recording,
no replay-by-request-hash.

Protocol:

1. The test POSTs one or more canned Anthropic assistant messages to
   ``/anthropic/ui/script`` together with a `test_id` — any unique string.
   The entries are enqueued FIFO.
2. The test sends its stimulus (e.g. a Slack mention) whose prompt contains
   that same `test_id` as a substring — usually the Slack message text will
   be something like ``[test-id: abc123] Clone ...``, so the ID appears in
   the ``Latest Mention Request`` section of the prompt and therefore in
   every LLM request body for that run.
3. When the agent calls ``POST /v1/messages``, this proxy scans the request
   body for any registered `test_id`. On a match it pops the next queued
   response for that ID and returns it verbatim.
4. No match, or empty queue → ``502 scripted_response_missing`` so the test
   fails loudly instead of timing out.

Response JSON must match the non-streaming Anthropic Messages API shape:
``{"id": ..., "role": "assistant", "content": [...], "stop_reason": ...}``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from . import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/anthropic")


async def _all_test_ids() -> list[str]:
    rows = await db.fetchall(
        "SELECT DISTINCT test_id FROM llm_scripts WHERE consumed = 0"
    )
    return [r["test_id"] for r in rows]


async def _pop_next(test_id: str) -> dict[str, Any] | None:
    row = await db.fetchone(
        "SELECT rowid, response_json, response_status "
        "FROM llm_scripts "
        "WHERE test_id = ? AND consumed = 0 "
        "ORDER BY seq ASC LIMIT 1",
        (test_id,),
    )
    if not row:
        return None
    await db.execute("UPDATE llm_scripts SET consumed = 1 WHERE rowid = ?", (row["rowid"],))
    return row


@router.post("/v1/messages")
async def messages(request: Request) -> Response:
    body = await request.body()
    body_text = body.decode("utf-8", errors="replace")

    for test_id in await _all_test_ids():
        if test_id in body_text:
            popped = await _pop_next(test_id)
            if popped is None:
                continue
            logger.info("scripted response for test_id=%s seq served", test_id)
            return Response(
                content=popped["response_json"].encode("utf-8"),
                status_code=popped["response_status"],
                media_type="application/json",
            )

    remaining = await _all_test_ids()
    msg = (
        f"No scripted response available. Request body did not match any "
        f"registered test_id. Registered (with queued responses): {remaining or '[]'}. "
        f"POST to /anthropic/ui/script first."
    )
    logger.warning(msg)
    return Response(
        status_code=502,
        content=json.dumps({"error": {"type": "scripted_response_missing", "message": msg}}).encode(),
        media_type="application/json",
    )


@router.post("/ui/script")
async def add_script(request: Request) -> dict[str, Any]:
    """Enqueue one or more scripted responses for a test_id.

    Body: ``{"test_id": "abc", "responses": [ <assistant message json>, ... ]}``
    Each response may be the raw Anthropic message JSON, or an object with
    ``{"status": 200, "body": {...}}`` to override the HTTP status.
    """
    body = await request.json()
    test_id = body.get("test_id")
    responses = body.get("responses") or []
    if not test_id or not isinstance(test_id, str):
        raise HTTPException(status_code=400, detail="test_id required (string)")
    if not isinstance(responses, list) or not responses:
        raise HTTPException(status_code=400, detail="responses must be a non-empty list")

    row = await db.fetchone(
        "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM llm_scripts WHERE test_id = ?",
        (test_id,),
    )
    next_seq = int((row or {}).get("next_seq") or 0)

    for idx, entry in enumerate(responses):
        if isinstance(entry, dict) and "body" in entry and "status" in entry:
            resp_json = json.dumps(entry["body"])
            status_code = int(entry["status"])
        else:
            resp_json = json.dumps(entry)
            status_code = 200
        await db.execute(
            "INSERT INTO llm_scripts (test_id, seq, response_json, response_status, "
            "consumed, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (test_id, next_seq + idx, resp_json, status_code, time.time()),
        )

    return {"ok": True, "test_id": test_id, "enqueued": len(responses), "first_seq": next_seq}


@router.delete("/ui/script/{test_id}")
async def clear_script(test_id: str) -> dict[str, Any]:
    await db.execute("DELETE FROM llm_scripts WHERE test_id = ?", (test_id,))
    return {"ok": True, "test_id": test_id}


@router.get("/ui/scripts")
async def list_scripts() -> dict[str, Any]:
    rows = await db.fetchall(
        "SELECT test_id, "
        "       SUM(CASE WHEN consumed = 0 THEN 1 ELSE 0 END) AS pending, "
        "       SUM(CASE WHEN consumed = 1 THEN 1 ELSE 0 END) AS consumed, "
        "       MIN(created_at) AS created_at "
        "  FROM llm_scripts "
        " GROUP BY test_id "
        " ORDER BY MIN(created_at)"
    )
    return {"scripts": rows}
