"""The run's tools MDA runs itself: those that read nothing of the backend's.

The backend still describes each, grants it and marks it (``ToolSpec.runtime_runs``);
this runtime runs a marked tool it has here in place of calling its operation, and
answers as the operation would, byte for byte (``rt.local_results``). A tool it has
not, or one that offloads a large result where the run has no sandbox of MDA's, goes
to the operation. The model's own requests go only to public addresses
(``rt.public_http``), and large results go to the sandbox MDA owns.
"""

import asyncio
import json
import logging
import posixpath
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, cast
from uuid import uuid4

import httpx2
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.tools import ToolRuntime
from markdownify import markdownify
from pydantic import ConfigDict, JsonValue, SkipValidation, TypeAdapter, validate_call
from runtime_api.catalog import ToolSpec
from runtime_api.operations import OperationResult
from runtime_api.sandbox_setup import WORK_DIR

from rt import local_results as results
from rt.backend import Run
from rt.prompts import prompt
from rt.public_http import BlockedUrlError, request_public
from rt.sandbox_setup import sandbox_setup

logger = logging.getLogger(__name__)

_USER_AGENT: Final = "Mozilla/5.0 (compatible; DeepAgents/1.0)"
_json = TypeAdapter(JsonValue)
_arbitrary = ConfigDict(arbitrary_types_allowed=True)


@dataclass(frozen=True)
class _Call:
    """One call of a tool MDA runs, with what it may reach besides its arguments."""

    spec: ToolSpec
    run: Run
    runtime: ToolRuntime
    credentials: Mapping[str, str]

    @property
    def sandbox(self) -> SandboxBackendProtocol | None:
        """The thread's sandbox, when MDA owns it."""
        files = getattr(self.runtime, "backend", None)
        if self.run.planned.sandbox is None or not isinstance(files, SandboxBackendProtocol):
            return None
        return files

    async def write_output(self, tool: str, content: str, extension: str) -> str:
        """Where ``content`` went in the sandbox, as the backend's ``write_sandbox_output`` writes it."""
        sandbox = self.sandbox
        if sandbox is None:
            raise RuntimeError("This run has no sandbox of MDA's")
        await sandbox_setup(self.run).booted()
        path = posixpath.join(WORK_DIR, f"{tool}-{uuid4().hex}.{extension}")
        written = await sandbox.awrite(path, content)
        if written.error:
            raise RuntimeError(str(written.error))
        return path


type _Content = str | dict[str, object]
type _Answer = _Content | tuple[_Content, dict[str, JsonValue]]


@dataclass(frozen=True)
class _LocalTool:
    run: Callable[[_Call, Mapping[str, JsonValue]], Awaitable[_Answer]]
    # Whether a large result goes to the sandbox, so a run without one of MDA's calls
    # the operation, which offloads it to the run's own files.
    offloads: bool = False


@validate_call(config=_arbitrary)
async def web_search(
    call: SkipValidation[_Call], /, query: str, num_results: int = 5, include_contents: bool = True
) -> _Answer:
    api_key = next(
        (
            call.credentials[ref.slug]
            for ref in call.spec.connections
            if ref.slug in call.credentials
        ),
        None,
    )
    if not api_key:
        logger.warning("exa_api_key_missing")
        return {
            "success": False,
            "error": "EXA_API_KEY is not configured. Please add it to your environment variables.",
        }
    try:
        from exa_py import Exa  # noqa: PLC0415 - heavy import, as the backend defers it

        client = Exa(api_key=api_key)
        if include_contents:
            found = await asyncio.to_thread(
                # The backend's call, whose result this one's must match.
                client.search_and_contents,  # ty: ignore[deprecated]
                query,
                text=True,
                num_results=num_results,
                type="auto",
            )
        else:
            found = await asyncio.to_thread(
                client.search, query, num_results=num_results, type="auto"
            )
        text = str(found)
        try:
            path = await call.write_output(
                "web-search", results.chunk_output_as_jsonl(text), "jsonl"
            )
        except Exception:
            logger.info("Web search sandbox unavailable; returning bounded inline results")
            return {
                "success": True,
                "results_path": None,
                "results": results.bounded_inline_results(text),
                "result_chars": len(text),
                "error": None,
            }
        return {
            "success": True,
            "results_path": path,
            "results": None,
            "result_chars": len(text),
            "error": None,
        }
    except Exception as exc:
        logger.exception("web_search failed")
        return {
            "success": False,
            "results_path": None,
            "results": None,
            "result_chars": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


@validate_call(config=_arbitrary)
async def fetch_url(call: SkipValidation[_Call], /, url: str, timeout: int = 30) -> _Answer:  # noqa: ARG001
    try:
        async with httpx2.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await request_public(client, "GET", url, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
            markdown = results.truncated_markdown(markdownify(response.text))
        return {
            "url": str(response.url),
            "markdown_content": markdown,
            "status_code": response.status_code,
            "content_length": len(markdown),
        }
    except BlockedUrlError as exc:
        return {"error": str(exc), "status_code": 0, "url": exc.url}
    except httpx2.HTTPError as exc:
        return {"error": f"Fetch URL error: {exc!s}", "url": url}


def _failed_request(url: str, content: str) -> dict[str, object]:
    return {"success": False, "status_code": 0, "headers": {}, "content": content, "url": url}


@validate_call(config=_arbitrary)
async def http_request(
    call: SkipValidation[_Call],
    /,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: str | dict | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 30,
) -> _Answer:
    sent: dict[str, object] = {}
    if headers:
        sent["headers"] = headers
    if params:
        sent["params"] = params
    if data:
        sent["json" if isinstance(data, dict) else "content"] = data
    try:
        async with httpx2.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await request_public(client, method, url, **sent)
    except BlockedUrlError as exc:
        return _failed_request(exc.url, str(exc))
    except httpx2.TimeoutException:
        return _failed_request(url, f"Request timed out after {timeout} seconds")
    except httpx2.HTTPError as exc:
        return _failed_request(url, f"Request error: {exc!s}")
    try:
        content: object = response.json()
    except ValueError:
        content = response.text
    answer = {
        "success": response.status_code < 400,
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "content": content,
        "url": str(response.url),
    }
    serialized = json.dumps(answer, ensure_ascii=False, indent=2, default=str)
    if len(serialized) <= results.MAX_INLINE_CHARS:
        return answer
    try:
        path = await call.write_output(
            "http-response", results.chunk_output_as_jsonl(serialized), "jsonl"
        )
    except Exception:
        logger.exception("Failed to save oversized HTTP response to sandbox")
        return {
            "success": False,
            "status_code": answer["status_code"],
            "headers": {},
            "content": "Response exceeded the inline limit and could not be saved to the sandbox",
            "url": answer["url"],
            "response_chars": len(serialized),
        }
    return {
        "success": answer["success"],
        "status_code": answer["status_code"],
        "url": answer["url"],
        "response_path": path,
        "response_chars": len(serialized),
    }


def _checked[T](answer: Callable[..., T], arguments: Mapping[str, JsonValue], **extra: str) -> T:
    """``answer`` for ``arguments``, checked against its signature as the backend's tool
    checks them."""
    return validate_call(answer)(**arguments, **extra)


async def _no_reply_needed(call: _Call, arguments: Mapping[str, JsonValue]) -> _Answer:
    answer = _checked(
        results.slack_no_reply_needed, arguments, next_step=prompt("runs/slack-turn-closed")
    )
    if answer["success"] is True:
        configurable = call.runtime.config.get("configurable") or {}
        logger.info(
            "Turn declared as needing no user-facing reply",
            extra={
                "agent_thread_id": configurable.get("thread_id"),
                "no_reply_reason": str(arguments.get("reason", "")).strip(),
            },
        )
    return answer


async def _record_human_input(_call: _Call, arguments: Mapping[str, JsonValue]) -> _Answer:
    text, summary = _checked(results.record_human_input, arguments)
    return text, {"human_input_summary": summary}


def _plain(answer: Callable[..., Mapping[str, object]]) -> _LocalTool:
    async def run(_call: _Call, arguments: Mapping[str, JsonValue]) -> _Answer:
        return dict(_checked(answer, arguments))

    return _LocalTool(run)


def _with_call(answer: Callable[..., Awaitable[_Answer]], *, offloads: bool = False) -> _LocalTool:
    async def run(call: _Call, arguments: Mapping[str, JsonValue]) -> _Answer:
        return await answer(call, **arguments)

    return _LocalTool(run, offloads=offloads)


_LOCAL_TOOLS: Final[Mapping[str, _LocalTool]] = {
    "web_search": _with_call(web_search, offloads=True),
    "fetch_url": _with_call(fetch_url),
    "http_request": _with_call(http_request, offloads=True),
    "slack_no_reply_needed": _LocalTool(_no_reply_needed),
    "cli_result": _plain(results.cli_result),
    "record_human_input": _LocalTool(_record_human_input),
    "note_on_diff": _plain(results.note_on_diff),
    "propose_review_comment": _plain(results.propose_review_comment),
    "propose_pr_review": _plain(results.propose_pr_review),
}


def runs_locally(spec: ToolSpec, run: Run, runtime: ToolRuntime) -> bool:
    """Whether this runtime runs ``spec``'s tool itself for this call."""
    tool = _LOCAL_TOOLS.get(spec.name)
    if not spec.runtime_runs or tool is None:
        return False
    return not tool.offloads or _Call(spec, run, runtime, {}).sandbox is not None


def _rejection(spec: ToolSpec, arguments: Mapping[str, JsonValue]) -> str | None:
    """Why ``arguments`` don't fit ``spec``'s schema, as the backend's operation says it."""
    schema = spec.parameters
    additional = schema.get("additionalProperties")
    if additional is True or isinstance(additional, dict):
        return None
    raw_properties = schema.get("properties")
    properties = sorted(raw_properties) if isinstance(raw_properties, dict) else []
    unexpected = sorted(set(arguments) - set(properties))
    if not unexpected:
        return None
    raw_required = schema.get("required")
    required = set(raw_required) if isinstance(raw_required, list) else set()
    accepted = ", ".join(f"{key}{'*' if key in required else ''}" for key in properties)
    return (
        f"Unexpected arguments for {spec.name}: {', '.join(unexpected)}. "
        f"Accepted parameters: {accepted or '(none)'} (* = required)"
    )


def _text(content: _Content) -> str:
    """``content`` as a tool's result carries it, as LangChain writes a tool's own."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError, ValueError:
        return str(content)


async def run_locally(
    spec: ToolSpec,
    run: Run,
    arguments: Mapping[str, JsonValue],
    runtime: ToolRuntime,
    credentials: Mapping[str, str],
) -> OperationResult:
    """One call of ``spec``'s tool, run here, as its operation would have answered."""
    if rejection := _rejection(spec, arguments):
        return OperationResult(content=rejection, status="error")
    tool = _LOCAL_TOOLS[spec.name]
    try:
        answer = await tool.run(_Call(spec, run, runtime, credentials), arguments)
    except Exception as exc:
        logger.warning("Operation failed", extra={"operation": spec.name}, exc_info=True)
        failure = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "status": "error",
            "name": spec.name,
        }
        return OperationResult(content=json.dumps(failure), status="error")
    content, state = answer if isinstance(answer, tuple) else (answer, {})
    return OperationResult(
        content=_text(content), state=cast(dict[str, JsonValue], _json.validate_python(state))
    )
