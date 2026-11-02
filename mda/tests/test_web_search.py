import json
from collections.abc import Mapping
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from managed_deepagents import ConnectionNotFoundError
from rt import web_search
from rt.backend import OpenSweBackend, Run, RunContext
from rt.web_search import EXA_CONNECTION, MAX_INLINE_CHARS, runs_here, web_search_tool
from runtime_api.catalog import Catalog, ToolSpec
from runtime_api.connections import ConnectionRef

_SPEC = ToolSpec(
    name="web_search",
    description="Search the web",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "num_results": {"type": "integer"},
            "include_contents": {"type": "boolean"},
        },
        "required": ["query"],
    },
)


def _runtime() -> ToolRuntime:
    return ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )


def _exa(
    monkeypatch: pytest.MonkeyPatch,
    answer: httpx2.Response | Exception,
) -> list[httpx2.Request]:
    """Exa answering every search with ``answer``; the requests it got."""
    sent: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(
        web_search,
        "_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    )
    return sent


def _connections(monkeypatch: pytest.MonkeyPatch, read: str | Exception) -> list[str]:
    """Agent Auth answering every lookup with ``read``; the slugs looked up."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    slugs: list[str] = []

    async def get(slug: str, owner: Mapping[str, object]) -> str:
        slugs.append(slug)
        if isinstance(read, Exception):
            raise read
        return read

    monkeypatch.setattr("rt.tools.connections", MagicMock(get=get))
    return slugs


async def _search(arguments: dict[str, object], spec: ToolSpec = _SPEC) -> ToolMessage:
    tool = web_search_tool(spec, OpenSweBackend())
    assert tool.coroutine is not None
    result = await tool.coroutine(runtime=_runtime(), **arguments)
    assert isinstance(result, ToolMessage)
    return result


def test_the_run_searches_itself_whatever_the_catalog_names() -> None:
    assert runs_here(_SPEC)
    assert not runs_here(_SPEC.model_copy(update={"name": "fetch_url"}))


async def test_a_search_sends_the_deployments_exa_key_only_to_exa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slugs = _connections(monkeypatch, "exa-key")
    sent = _exa(
        monkeypatch,
        httpx2.Response(
            200,
            json={
                "results": [
                    {"title": "LangGraph", "url": "https://langchain.com", "text": "Agents"},
                    {"title": "No text", "url": "https://example.com"},
                ]
            },
        ),
    )
    result = await _search({"query": "langgraph", "num_results": 2})

    assert slugs == [EXA_CONNECTION.slug]
    [request] = sent
    assert (request.url, request.headers["x-api-key"]) == ("https://api.exa.ai/search", "exa-key")
    assert json.loads(request.content) == {
        "query": "langgraph",
        "numResults": 2,
        "type": "auto",
        "contents": {"text": True},
    }
    answer = json.loads(result.text)
    assert result.status == "success"
    assert answer["results"] == (
        "Title: LangGraph\nURL: https://langchain.com\nText: Agents\n\n"
        "Title: No text\nURL: https://example.com"
    )
    assert answer["result_chars"] == len(answer["results"])


async def test_a_catalog_naming_another_connection_reads_that_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slugs = _connections(monkeypatch, "exa-key")
    _exa(monkeypatch, httpx2.Response(200, json={"results": []}))
    spec = _SPEC.model_copy(
        update={"connections": [ConnectionRef(slug="exa-elsewhere", owner="agent")]}
    )
    result = await _search({"query": "q", "include_contents": False}, spec)
    assert (result.status, slugs) == ("success", ["exa-elsewhere"])


async def test_a_deployment_without_the_connection_is_told_how_to_create_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connections(monkeypatch, ConnectionNotFoundError("open-swe-exa", "agent"))
    sent = _exa(monkeypatch, AssertionError("searched without a key"))
    result = await _search({"query": "q"})
    assert result.status == "error"
    assert "mda connections create open-swe-exa" in result.text
    assert sent == []


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx2.Response(401, json={"error": "bad key exa-key"}), "Exa answered 401"),
        (httpx2.ConnectError("exa-key"), "Exa could not be reached (ConnectError)"),
        (httpx2.Response(200, text="not json"), "Exa's answer was not a list of results"),
    ],
)
async def test_a_failed_search_says_why_and_never_quotes_the_key(
    monkeypatch: pytest.MonkeyPatch, answer: httpx2.Response | Exception, error: str
) -> None:
    _connections(monkeypatch, "exa-key")
    _exa(monkeypatch, answer)
    result = await _search({"query": "q"})
    assert result.status == "error"
    assert json.loads(result.text)["error"] == error
    assert "exa-key" not in result.text


async def test_large_results_are_bounded_and_bad_arguments_never_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connections(monkeypatch, "exa-key")
    sent = _exa(
        monkeypatch, httpx2.Response(200, json={"results": [{"text": "x" * MAX_INLINE_CHARS}]})
    )
    answer = json.loads((await _search({"query": "q", "num_results": 500})).text)
    assert answer["result_chars"] == MAX_INLINE_CHARS + len("Text: ")
    assert answer["results"].endswith(
        f"[results truncated: {MAX_INLINE_CHARS}/{answer['result_chars']} chars]\n"
    )
    assert json.loads(sent[0].content)["numResults"] == 25

    result = await _search({"query": ""})
    assert (result.status, len(sent)) == ("error", 1)


async def test_code_mode_reports_the_search_it_makes(monkeypatch: pytest.MonkeyPatch) -> None:
    from rt.backend import reporting, subagent_of

    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    _connections(monkeypatch, "exa-key")
    _exa(monkeypatch, httpx2.Response(200, json={"results": []}))
    # The backend as a run of its own, as code mode calls it from one.
    backend = Run(
        RunContext.model_validate(
            {"__run_token": "token", "__catalog": Catalog().model_dump(mode="json")}
        )
    ).backend
    started, finished = MagicMock(), MagicMock()
    monkeypatch.setattr(backend.outbox, "started", started)
    monkeypatch.setattr(backend.outbox, "finished", finished)
    monkeypatch.setattr("rt.tools.cited", AsyncMock())
    tool = web_search_tool(_SPEC, backend)
    assert tool.coroutine is not None
    runtime = _runtime()
    runtime.tool_call_id = None
    with subagent_of("eval-1"), reporting("eval-1"):
        result = await tool.coroutine(runtime=runtime, query="q")
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id.startswith("eval-1:web_search:")
    assert started.call_count == finished.call_count == 1
