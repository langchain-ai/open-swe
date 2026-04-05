"""Anthropic API proxy that routes requests through the Claude Code CLI.

Translates ``POST /v1/messages`` requests into ``claude --print`` subprocess
calls, using the local OAuth session in ``~/.claude/`` instead of a direct
API key.  Responses are translated back to the Anthropic REST format,
including streaming (SSE) and tool-use support via prompt-engineered XML
``<tool_call>`` blocks.

Typical usage::

    # Start the proxy
    python -m agent.claude_code_proxy          # default: 127.0.0.1:9999
    python -m agent.claude_code_proxy --port 8888

    # Point your Anthropic client at the proxy
    ANTHROPIC_BASE_URL=http://localhost:9999
    ANTHROPIC_API_KEY=local-proxy   # any non-empty string works

This is intended for local development when you have a Claude Max subscription
(OAuth token) but need the agent to call the Anthropic API directly.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Claude Code Proxy")

__all__ = ["app"]

# ── Tool-call prompt injection ────────────────────────────────────────────────

_TOOL_SYSTEM_PREFIX = (
    "You have access to the following tools. When you want to call a tool, output "
    "EXACTLY this XML block (and nothing else on those lines):\n\n"
    "<tool_call>\n"
    '{"name": "<tool_name>", "id": "<unique_id>", "input": {<json_arguments>}}\n'
    "</tool_call>\n\n"
    "You may call multiple tools in one response by outputting multiple <tool_call> blocks.\n"
    "After the tool results are provided, continue your task.\n\n"
    "Available tools:\n"
)


def _tool_def_to_text(tool: dict[str, Any]) -> str:
    name = tool.get("name", "")
    desc = tool.get("description", "")
    schema = tool.get("input_schema", {})
    props = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    lines = [f"### {name}", desc, "Parameters:"]
    for pname, pdef in props.items():
        req = " (required)" if pname in required else ""
        ptype = pdef.get("type", "any")
        pdesc = pdef.get("description", "")
        lines.append(f"  - {pname} ({ptype}{req}): {pdesc}")
    return "\n".join(lines)


def _build_system_prompt(system: str | list[Any] | None, tools: list[dict[str, Any]]) -> str:
    """Merge the caller's system prompt with injected tool descriptions."""
    parts: list[str] = []

    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
    elif system:
        parts.append(system)

    if tools:
        tool_descriptions = "\n\n".join(_tool_def_to_text(t) for t in tools)
        parts.append(_TOOL_SYSTEM_PREFIX + tool_descriptions)

    return "\n\n".join(parts)


# ── Message serialisation ─────────────────────────────────────────────────────


def _content_to_text(content: Any) -> str:
    """Flatten a message content value to plain text for the CLI prompt."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block["text"])
            elif btype == "tool_use":
                call = {"name": block["name"], "id": block["id"], "input": block.get("input", {})}
                parts.append(f"<tool_call>\n{json.dumps(call)}\n</tool_call>")
            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = " ".join(
                        c.get("text", "") for c in result_content if isinstance(c, dict)
                    )
                tid = block.get("tool_use_id", "")
                parts.append(f'<tool_result tool_use_id="{tid}">\n{result_content}\n</tool_result>')
        return "\n".join(parts)
    return str(content)


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Format the messages list as a Human/Assistant dialogue for the CLI."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = _content_to_text(msg.get("content", ""))
        prefix = "Human" if role == "user" else "Assistant"
        lines.append(f"{prefix}: {text}")
    return "\n\n".join(lines)


# ── Tool-call response parsing ────────────────────────────────────────────────

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _parse_response(raw_text: str) -> list[dict[str, Any]]:
    """Split raw Claude output into text and tool_use content blocks."""
    blocks: list[dict[str, Any]] = []
    last_end = 0

    for match in _TOOL_CALL_RE.finditer(raw_text):
        pre = raw_text[last_end : match.start()].strip()
        if pre:
            blocks.append({"type": "text", "text": pre})

        try:
            call = json.loads(match.group(1))
            tool_id = call.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": call["name"],
                    "input": call.get("input", {}),
                }
            )
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Failed to parse tool_call JSON: %s – %s", match.group(1)[:100], exc)
            blocks.append({"type": "text", "text": match.group(0)})

        last_end = match.end()

    tail = raw_text[last_end:].strip()
    if tail:
        blocks.append({"type": "text", "text": tail})

    if not blocks:
        blocks.append({"type": "text", "text": raw_text.strip()})

    return blocks


# ── Claude CLI invocation ─────────────────────────────────────────────────────

_DEFAULT_TIMEOUT = 300


def _call_claude(prompt: str, system: str, model: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Invoke ``claude --print`` and return the result text."""
    cmd = ["claude", "--print", "--output-format", "json"]
    if model:
        cmd += ["--model", model]

    cli_input = f"[SYSTEM]\n{system}\n[/SYSTEM]\n\n{prompt}" if system else prompt

    logger.info("Calling claude CLI (model=%s, prompt_len=%d)", model, len(cli_input))
    t0 = time.monotonic()

    result = subprocess.run(
        cmd,
        input=cli_input,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    elapsed = time.monotonic() - t0
    logger.info("claude CLI returned in %.1fs (exit=%d)", elapsed, result.returncode)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("claude CLI error: %s", stderr)
        raise RuntimeError(f"claude CLI failed: {stderr}")

    try:
        data = json.loads(result.stdout)
        return data.get("result", result.stdout.strip())
    except json.JSONDecodeError:
        return result.stdout.strip()


# ── SSE streaming helpers ─────────────────────────────────────────────────────


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _iter_sse_stream(
    msg_id: str,
    model: str,
    content_blocks: list[dict[str, Any]],
    raw_text: str,
):
    """Yield Anthropic SSE events for the given content blocks."""
    has_tool_use = any(b["type"] == "tool_use" for b in content_blocks)
    stop_reason = "tool_use" if has_tool_use else "end_turn"
    input_tokens = max(1, len(raw_text) // 4)
    output_tokens = max(1, len(raw_text) // 4)

    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 1},
            },
        },
    )
    yield _sse("ping", {"type": "ping"})

    for idx, block in enumerate(content_blocks):
        if block["type"] == "text":
            yield _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": block["text"]},
                },
            )
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})

        elif block["type"] == "tool_use":
            yield _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {},
                    },
                },
            )
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block.get("input", {})),
                    },
                },
            )
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})

    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


# ── API endpoints ─────────────────────────────────────────────────────────────


@app.post("/v1/messages")
async def messages(request: Request) -> StreamingResponse | JSONResponse:
    """Handle Anthropic ``/v1/messages`` requests via the Claude Code CLI."""
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": str(exc)})

    messages_list: list[dict[str, Any]] = body.get("messages", [])
    system_raw = body.get("system")
    tools: list[dict[str, Any]] = body.get("tools", [])
    model: str = body.get("model", "claude-sonnet-4-6")
    stream: bool = body.get("stream", False)

    system_text = _build_system_prompt(system_raw, tools)
    prompt = _messages_to_prompt(messages_list)

    try:
        raw_text = _call_claude(prompt, system_text, model)
    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=504, content={"error": "claude CLI timed out"})
    except RuntimeError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})

    content_blocks = _parse_response(raw_text)
    has_tool_use = any(b["type"] == "tool_use" for b in content_blocks)
    stop_reason = "tool_use" if has_tool_use else "end_turn"
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    if stream:
        return StreamingResponse(
            _iter_sse_stream(msg_id, model, content_blocks, raw_text),
            media_type="text/event-stream",
        )

    return JSONResponse(
        content={
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": max(1, len(prompt) // 4),
                "output_tokens": max(1, len(raw_text) // 4),
            },
        }
    )


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    """Return the Claude models that the proxy supports."""
    return JSONResponse(
        content={
            "data": [
                {"id": "claude-opus-4-6", "object": "model"},
                {"id": "claude-sonnet-4-6", "object": "model"},
                {"id": "claude-haiku-4-5-20251001", "object": "model"},
            ]
        }
    )


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness probe."""
    return JSONResponse(content={"ok": True})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Code local Anthropic API proxy")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9999, help="Bind port (default: 9999)")
    args = parser.parse_args()

    logger.info("Starting Claude Code proxy on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
