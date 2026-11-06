import gzip
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final
from unittest.mock import MagicMock

import httpx2
import pytest
from deepagents.backends.local_shell import LocalShellBackend
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, JsonValue
from rt import public_http
from rt.backend import OpenSweBackend, Run, RunContext, reporting, subagent_of
from rt.public_http import BlockedUrlError, request_public, truncated
from rt.tools import STATE_KEY, operation_tool
from runtime_api.catalog import Catalog, SandboxCreate, SandboxSpec, ToolSpec
from runtime_api.endpoint import Delivery
from runtime_api.operations import OperationResult

_IN_SANDBOX: Final = SandboxSpec(
    create=SandboxCreate(fs_capacity_bytes=1, idle_ttl_seconds=60, delete_after_stop_seconds=60)
)
_PUBLIC: Final = {"api.example.com": "93.184.216.34", "cdn.example.net": "93.184.216.35"}
_PRIVATE: Final = {"metadata.example.com": "169.254.169.254"}


@pytest.fixture(autouse=True)
def _backend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")


@dataclass
class _SandboxRuntime(ToolRuntime):
    """A tool's runtime as MDA hands it, with the thread's sandbox."""

    backend: object = None


def _run_backend(catalog: Catalog) -> OpenSweBackend:
    context = RunContext.model_validate(
        {"__run_token": "secret-token", "__catalog": catalog.model_dump(mode="json")}
    )
    return Run(context).backend


def _operations(monkeypatch: pytest.MonkeyPatch, backend: OpenSweBackend) -> list[str]:
    """The operations ``backend`` is asked for, each answered ``"from the backend"``."""
    asked: list[str] = []

    async def post(
        path: str,
        body: dict[str, JsonValue],
        *,
        timeout: float,
        delivery: Delivery,
        headers: Mapping[str, str] | None = None,
    ) -> JsonValue:
        asked.append(str(body.get("name")))
        return OperationResult(content="from the backend").model_dump(mode="json")

    monkeypatch.setattr(backend, "post", post)
    return asked


def _runtime(tool_call_id: str | None = "call-1", sandbox: object = None) -> _SandboxRuntime:
    return _SandboxRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda _event: None,
        tool_call_id=tool_call_id,
        store=None,
        backend=sandbox,
    )


def _resolving(monkeypatch: pytest.MonkeyPatch, hosts: Mapping[str, str]) -> None:
    def getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple]:
        if host not in hosts:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hosts[host], 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


class _Sent(BaseModel):
    host: str
    address: str
    headers: dict[str, str]


def _serving(monkeypatch: pytest.MonkeyPatch, routes: Mapping[str, httpx2.Response]) -> list[_Sent]:
    """Answer each request by its ``Host`` and path, recording where it went."""
    sent: list[_Sent] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        host = request.headers["Host"]
        sent.append(
            _Sent(host=host, address=request.url.host, headers=dict(request.headers.items()))
        )
        return routes[f"{host}{request.url.path}"]

    monkeypatch.setattr(
        httpx2, "AsyncClient", partial(httpx2.AsyncClient, transport=httpx2.MockTransport(handle))
    )
    return sent


@pytest.mark.parametrize("marked", [True, False])
@pytest.mark.parametrize("code_mode", [False, True])
async def test_a_tool_the_backend_marks_runs_here_and_answers_as_its_operation(
    monkeypatch: pytest.MonkeyPatch, marked: bool, code_mode: bool
) -> None:
    backend = _run_backend(Catalog())
    asked = _operations(monkeypatch, backend)
    started = MagicMock()
    finished = MagicMock()
    monkeypatch.setattr(backend.outbox, "started", started)
    monkeypatch.setattr(backend.outbox, "finished", finished)
    spec = ToolSpec(
        name="record_human_input",
        description="Record",
        parameters={"type": "object", "properties": {"summary": {"type": "string"}}},
        runtime_runs=marked,
    )
    served = operation_tool(spec, backend)
    assert served.coroutine is not None

    if code_mode:
        with subagent_of("eval-1"), reporting("eval-1"):
            result = await served.coroutine(runtime=_runtime(None), summary="  Needs X  ")
    else:
        result = await served.coroutine(runtime=_runtime(), summary="  Needs X  ")

    assert asked == ([] if marked else ["record_human_input"])
    assert started.call_count == finished.call_count == code_mode
    if not marked:
        return
    # The operation's answer: the tool's text, and the summary in the run's state.
    assert isinstance(result, Command)
    assert result.update == {
        "messages": [result.update["messages"][0]],
        STATE_KEY: {"human_input_summary": "Needs X"},
    }
    [message] = result.update["messages"]
    assert isinstance(message, ToolMessage)
    assert (message.content, message.name, message.status) == (
        "Recorded.",
        "record_human_input",
        "success",
    )


async def test_arguments_the_tools_schema_does_not_take_are_refused_as_the_backend_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _run_backend(Catalog())
    spec = ToolSpec(
        name="propose_pr_review",
        description="Propose",
        parameters={
            "type": "object",
            "properties": {"event": {"type": "string"}, "body": {"type": "string"}},
            "required": ["event"],
        },
        runtime_runs=True,
    )
    served = operation_tool(spec, backend)
    assert served.coroutine is not None

    result = await served.coroutine(runtime=_runtime(), event="APPROVE", verdict="ship it")

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.content == (
        "Unexpected arguments for propose_pr_review: verdict. "
        "Accepted parameters: body, event* (* = required)"
    )


@pytest.mark.parametrize(
    ("url", "error"),
    [
        (
            "https://api.example.com/to-metadata",
            "Request blocked: URL resolves to blocked address: 169.254.169.254",
        ),
        ("file:///etc/passwd", "Request blocked: Unsupported URL scheme: file"),
        (
            "https://nowhere.example.org/",
            "Request blocked: Could not resolve hostname: nowhere.example.org",
        ),
    ],
)
async def test_a_request_goes_to_no_private_address_even_when_redirected(
    monkeypatch: pytest.MonkeyPatch, url: str, error: str
) -> None:
    _resolving(monkeypatch, {**_PUBLIC, **_PRIVATE})
    sent = _serving(
        monkeypatch,
        {
            "api.example.com/to-metadata": httpx2.Response(
                302, headers={"Location": "http://metadata.example.com/latest/meta-data"}
            )
        },
    )

    async with httpx2.AsyncClient() as client:
        with pytest.raises(BlockedUrlError) as raised:
            await request_public(client, "GET", url)

    assert str(raised.value) == error
    assert all(request.address in _PUBLIC.values() for request in sent)


async def test_a_redirect_to_another_origin_drops_the_credentials_the_request_carried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolving(monkeypatch, _PUBLIC)
    sent = _serving(
        monkeypatch,
        {
            "api.example.com/start": httpx2.Response(307, headers={"Location": "/same-origin"}),
            "api.example.com/same-origin": httpx2.Response(
                302, headers={"Location": "https://cdn.example.net/file"}
            ),
            "cdn.example.net/file": httpx2.Response(200, text="done"),
        },
    )
    headers = {"Authorization": "Bearer secret", "X-Api-Key": "key", "Accept": "text/plain"}

    async with httpx2.AsyncClient() as client:
        response = await request_public(
            client, "GET", "https://api.example.com/start", headers=headers
        )

    assert response.text == "done"
    assert [(request.host, request.address) for request in sent] == [
        ("api.example.com", "93.184.216.34"),
        ("api.example.com", "93.184.216.34"),
        ("cdn.example.net", "93.184.216.35"),
    ]
    assert [{"authorization", "x-api-key"} <= request.headers.keys() for request in sent] == [
        True,
        True,
        False,
    ]
    assert sent[-1].headers["accept"] == "text/plain"


async def test_a_response_is_read_only_up_to_its_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host serving a huge body can't exhaust the worker: reading stops at the limit."""
    _resolving(monkeypatch, _PUBLIC)
    monkeypatch.setattr(public_http, "MAX_RESPONSE_BYTES", 10)
    _serving(
        monkeypatch,
        {
            "api.example.com/big": httpx2.Response(200, content=b"x" * 100),
            "api.example.com/small": httpx2.Response(
                200, content=gzip.compress(b"hello"), headers={"Content-Encoding": "gzip"}
            ),
        },
    )

    async with httpx2.AsyncClient() as client:
        big = await request_public(client, "GET", "https://api.example.com/big")
        small = await request_public(client, "GET", "https://api.example.com/small")

    assert (big.content, truncated(big)) == (b"x" * 10, True)
    # The body is decoded as it is read, so its encoding headers no longer describe it.
    assert (small.text, truncated(small), "content-encoding" in small.headers) == (
        "hello",
        False,
        False,
    )


@pytest.mark.parametrize("in_sandbox", [True, False])
async def test_a_large_response_goes_to_the_sandbox_mda_owns_or_the_backend_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, in_sandbox: bool
) -> None:
    _resolving(monkeypatch, _PUBLIC)
    body = {"items": ["x" * 1_000] * 120}
    _serving(monkeypatch, {"api.example.com/items": httpx2.Response(200, json=body)})
    backend = _run_backend(Catalog(sandbox=_IN_SANDBOX if in_sandbox else None))
    asked = _operations(monkeypatch, backend)
    spec = ToolSpec(
        name="http_request",
        description="Request",
        parameters={"type": "object", "properties": {"url": {"type": "string"}}},
        runtime_runs=True,
    )
    served = operation_tool(spec, backend)
    assert served.coroutine is not None
    sandbox = LocalShellBackend(root_dir=tmp_path, virtual_mode=True)

    result = await served.coroutine(
        runtime=_runtime(sandbox=sandbox), url="https://api.example.com/items"
    )

    assert isinstance(result, ToolMessage)
    if not in_sandbox:
        # Without a sandbox of MDA's, the backend offloads it to the run's own files.
        assert (asked, result.content) == (["http_request"], "from the backend")
        return
    assert asked == []
    answer = json.loads(result.text)
    path = answer.pop("response_path")
    assert answer == {
        "success": True,
        "status_code": 200,
        "url": "https://93.184.216.34/items",
        "response_chars": answer["response_chars"],
    }
    assert path.startswith("/workspace/http-response-") and path.endswith(".jsonl")
    chunks = (tmp_path / path.removeprefix("/")).read_text().splitlines()
    offloaded = "".join(json.loads(chunk)["text"] for chunk in chunks)
    assert json.loads(offloaded)["content"] == body
    assert len(offloaded) == answer["response_chars"]
