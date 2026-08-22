"""One in-memory stand-in for ``httpx.AsyncClient``.

Several dashboard endpoints are thin proxies: they build a request for the
LangGraph platform and hand its response back. What a test needs to see is the
request that was built -- URL, headers, body -- so ``FakeHttpx`` records every
request it is given in :attr:`FakeHttpx.requests` and replays one canned
response for all of them::

    proxy = FakeHttpx(content=b'{"type": "success", "result": {"run_id": "r1"}}')
    monkeypatch.setattr(thread_proxy.httpx, "AsyncClient", proxy.client)

``chunks`` makes the canned response streamable through ``client.stream(...)``;
with none given a streamed response yields its ``content`` as a single chunk.
"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Request:
    """One request the code under test handed to ``httpx``."""

    method: str
    url: str
    headers: dict[str, str]
    content: bytes | None = None
    json_body: Any = None
    params: dict[str, Any] | None = None

    @property
    def payload(self) -> Any:
        """The body as JSON, whether it was sent as raw bytes or as ``json=``."""
        if self.content is None:
            return self.json_body
        return json.loads(self.content)


class FakeHttpxResponse:
    def __init__(
        self,
        *,
        status_code: int,
        content: bytes,
        headers: dict[str, str],
        chunks: Sequence[bytes],
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers
        self.reason_phrase = ""
        self._chunks = list(chunks) or [content]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://langgraph.test/")
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    async def aread(self) -> bytes:
        return self.content

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _StreamContext:
    def __init__(self, response: FakeHttpxResponse) -> None:
        self._response = response

    async def __aenter__(self) -> FakeHttpxResponse:
        return self._response

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeHttpx:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"{}",
        headers: dict[str, str] | None = None,
        chunks: Sequence[bytes] = (),
    ) -> None:
        self.requests: list[Request] = []
        self._response = FakeHttpxResponse(
            status_code=status_code,
            content=content,
            headers=headers if headers is not None else {"content-type": "application/json"},
            chunks=chunks,
        )

    @property
    def payloads(self) -> list[Any]:
        return [request.payload for request in self.requests]

    def record(
        self,
        method: str,
        url: str,
        *,
        content: bytes | None,
        json_body: Any,
        headers: dict[str, str] | None,
        params: dict[str, Any] | None,
    ) -> FakeHttpxResponse:
        self.requests.append(
            Request(
                method=method,
                url=url,
                headers=dict(headers or {}),
                content=content,
                json_body=json_body,
                params=params,
            )
        )
        return self._response

    @property
    def client(self) -> type:
        """The class to patch in place of ``httpx.AsyncClient``."""
        recorder = self

        class _FakeAsyncClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def post(
                self,
                url: str,
                *,
                content: bytes | None = None,
                json: Any = None,
                headers: dict[str, str] | None = None,
                params: dict[str, Any] | None = None,
            ) -> FakeHttpxResponse:
                return recorder.record(
                    "POST", url, content=content, json_body=json, headers=headers, params=params
                )

            def stream(
                self,
                method: str,
                url: str,
                *,
                content: bytes | None = None,
                headers: dict[str, str] | None = None,
                params: dict[str, Any] | None = None,
                **_kwargs: object,
            ) -> _StreamContext:
                return _StreamContext(
                    recorder.record(
                        method, url, content=content, json_body=None, headers=headers, params=params
                    )
                )

        return _FakeAsyncClient
