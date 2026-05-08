#!/usr/bin/env python
"""Dump the most recent LangGraph thread in a readable format.

Usage:
    uv run python -m hack.dump_thread [<thread_id>]

With no argument, picks the most-recently-updated thread.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL", "http://localhost:2025")


def _fmt_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            t = block.get("type")
            if t == "text":
                parts.append(block.get("text", ""))
            elif t == "tool_use":
                name = block.get("name", "?")
                args = json.dumps(block.get("input", {}), indent=2)
                parts.append(f"[tool_use: {name}]\n{args}")
            elif t == "tool_result":
                parts.append(f"[tool_result]\n{_fmt_content(block.get('content'))}")
            elif t == "image":
                parts.append("[image omitted]")
            else:
                parts.append(json.dumps(block, indent=2))
        return "\n".join(parts)
    return json.dumps(content, indent=2)


def _role_header(msg: dict[str, Any]) -> str:
    role = msg.get("type") or msg.get("role") or "?"
    name = msg.get("name")
    tag = f"{role}:{name}" if name else role
    return tag


def _fmt_tool_calls(tool_calls: list[dict[str, Any]]) -> str:
    if not tool_calls:
        return ""
    lines = []
    for tc in tool_calls:
        name = tc.get("name", "?")
        args = tc.get("args") or tc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                pass
        args_str = json.dumps(args, indent=2) if isinstance(args, (dict, list)) else str(args)
        lines.append(f"→ tool_call: {name}\n{args_str}")
    return "\n".join(lines)


def _pick_thread_id(explicit: str | None) -> str:
    if explicit:
        return explicit
    resp = httpx.post(
        f"{LANGGRAPH_URL}/threads/search",
        json={"limit": 1, "sort_by": "updated_at", "sort_order": "desc"},
        timeout=5.0,
    )
    resp.raise_for_status()
    threads = resp.json()
    if not threads:
        sys.exit("no threads found")
    return threads[0]["thread_id"]


def main() -> None:
    thread_id = _pick_thread_id(sys.argv[1] if len(sys.argv) > 1 else None)
    state = httpx.get(f"{LANGGRAPH_URL}/threads/{thread_id}/state", timeout=10.0).json()
    thread = httpx.get(f"{LANGGRAPH_URL}/threads/{thread_id}", timeout=5.0).json()
    runs = httpx.get(f"{LANGGRAPH_URL}/threads/{thread_id}/runs", timeout=5.0).json()

    print(f"=== thread {thread_id} ===")
    print(f"status:     {thread.get('status')}")
    print(f"updated:    {thread.get('updated_at')}")
    meta = thread.get("metadata") or {}
    if meta:
        print(f"metadata:   {json.dumps({k: v for k, v in meta.items() if not k.endswith('_encrypted')}, indent=2)}")
    print(f"runs:       {len(runs)}")
    for r in runs[-5:]:
        print(f"  - {r.get('run_id')} | {r.get('status')} | {r.get('created_at')}")
    print()

    messages = (state.get("values") or {}).get("messages") or []
    print(f"=== messages ({len(messages)}) ===")
    for i, m in enumerate(messages):
        print(f"--- [{i}] {_role_header(m)} ---")
        body = _fmt_content(m.get("content"))
        if body:
            print(body)
        tc = _fmt_tool_calls(m.get("tool_calls") or [])
        if tc:
            print(tc)
        print()


if __name__ == "__main__":
    main()
