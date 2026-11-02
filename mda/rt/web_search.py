"""``web_search``, run here: the run calls Exa itself, with the deployment's Exa key.

The key is the deployment's agent connection ``open-swe-exa`` (``mda/CONNECTIONS.md``),
or the one the catalog names; the run reads it as it reads any operation's
(``rt.tools``) and sends it only to Exa. The backend's operation of that name serves
Open SWE's own MCP server alone. A large result is left to the graph, which offloads
it to the run's files as it does any tool's.
"""

import json
import logging
from collections.abc import Mapping
from functools import partial
from typing import Final

import httpx2
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field, JsonValue, ValidationError
from runtime_api.catalog import ToolSpec
from runtime_api.connections import ConnectionRef

from rt.backend import OpenSweBackend
from rt.tools import UnreadConnectionError, called, read_credentials

logger = logging.getLogger(__name__)

NAME: Final = "web_search"
# The deployment's connection holding the Exa key, where the catalog names none.
EXA_CONNECTION: Final = ConnectionRef(slug="open-swe-exa", owner="agent")
_EXA_SEARCH_URL: Final = "https://api.exa.ai/search"
_TIMEOUT_SECONDS: Final = 60.0
# As the backend's: what the model reads of the results, which the graph offloads
# to the run's files past its own limit.
MAX_INLINE_CHARS: Final = 100_000
# Exa's answer, read no further than this.
_MAX_RESPONSE_BYTES: Final = 4_000_000
_MAX_RESULTS: Final = 25


class _Arguments(BaseModel):
    query: str = Field(min_length=1)
    num_results: int = 5
    include_contents: bool = True


class _Result(BaseModel):
    title: str | None = None
    url: str | None = None
    publishedDate: str | None = None  # noqa: N815 - Exa's field name
    author: str | None = None
    text: str | None = None


class _Answer(BaseModel):
    results: list[_Result] = []


class ExaError(RuntimeError):
    """Exa did not answer the search; its message names no credential."""


def _client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(timeout=httpx2.Timeout(_TIMEOUT_SECONDS), trust_env=False)


async def search(api_key: str, arguments: _Arguments) -> str:
    """The results of the search ``arguments`` name, as the backend's tool gives them."""
    body: dict[str, JsonValue] = {
        "query": arguments.query,
        "numResults": max(1, min(arguments.num_results, _MAX_RESULTS)),
        "type": "auto",
    }
    if arguments.include_contents:
        body["contents"] = {"text": True}
    async with _client() as client:
        try:
            async with client.stream(
                "POST", _EXA_SEARCH_URL, json=body, headers={"x-api-key": api_key}
            ) as response:
                if response.status_code != 200:
                    raise ExaError(f"Exa answered {response.status_code}")
                read = bytearray()
                async for chunk in response.aiter_bytes():
                    read += chunk
                    if len(read) > _MAX_RESPONSE_BYTES:
                        raise ExaError("Exa's answer was too large to read")
        except httpx2.HTTPError as exc:
            raise ExaError(f"Exa could not be reached ({type(exc).__name__})") from exc
    try:
        answer = _Answer.model_validate_json(bytes(read))
    except ValidationError as exc:
        raise ExaError("Exa's answer was not a list of results") from exc
    return "\n\n".join(_described(result) for result in answer.results)


def _described(result: _Result) -> str:
    fields = (
        ("Title", result.title),
        ("URL", result.url),
        ("Published Date", result.publishedDate),
        ("Author", result.author),
        ("Text", result.text),
    )
    return "\n".join(f"{label}: {value}" for label, value in fields if value)


def _bounded(results: str) -> str:
    if len(results) <= MAX_INLINE_CHARS:
        return results
    return (
        results[:MAX_INLINE_CHARS]
        + f"\n... [results truncated: {MAX_INLINE_CHARS}/{len(results)} chars]\n"
    )


def _message(tool_call_id: str, answer: Mapping[str, JsonValue]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(answer, ensure_ascii=False),
        tool_call_id=tool_call_id,
        name=NAME,
        status="success" if answer.get("success") else "error",
    )


async def _searched(
    spec: ToolSpec, api_key: str, arguments: Mapping[str, JsonValue], tool_call_id: str
) -> ToolMessage:
    try:
        parsed = _Arguments.model_validate(arguments)
    except ValidationError as exc:
        error = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _message(tool_call_id, {"success": False, "error": f"Invalid arguments: {error}"})
    try:
        results = await search(api_key, parsed)
    except ExaError as exc:
        logger.warning("Exa did not answer a web search", extra={"reason": str(exc)})
        return _message(
            tool_call_id,
            {"success": False, "results": None, "result_chars": 0, "error": str(exc)},
        )
    return _message(
        tool_call_id,
        {
            "success": True,
            "results": _bounded(results),
            "result_chars": len(results),
            "error": None,
        },
    )


def web_search_tool(
    spec: ToolSpec, backend: OpenSweBackend, *, cites_evidence: bool = False
) -> StructuredTool:
    """``web_search`` as the catalog describes it, run here against Exa."""
    if not spec.connections:
        spec = spec.model_copy(update={"connections": [EXA_CONNECTION]})

    async def run(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
        # Read before code mode reports the call, as an operation's credentials are.
        try:
            credentials = await read_credentials(spec)
        except UnreadConnectionError as exc:
            make = exc.result
        else:
            api_key = next(iter(credentials.values()), None)
            if api_key is None:
                make = UnreadConnectionError(
                    "Error: web_search has no Exa key to search with, so it did not run."
                ).result
            else:
                make = partial(_searched, spec, api_key, arguments)
        return await called(backend, NAME, arguments, runtime, make, cites_evidence=cites_evidence)

    return StructuredTool.from_function(
        coroutine=run,
        name=spec.name,
        description=spec.description,
        args_schema=spec.parameters,
    )


def runs_here(spec: ToolSpec) -> bool:
    """Whether ``spec`` is the operation this runtime runs itself."""
    return spec.name == NAME
