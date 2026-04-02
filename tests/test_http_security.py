from __future__ import annotations

import importlib
import sys
import types

import requests

exa_py_stub = types.ModuleType("exa_py")
exa_py_stub.Exa = object
sys.modules.setdefault("exa_py", exa_py_stub)

importlib.import_module("agent.tools.fetch_url")
importlib.import_module("agent.tools.http_request")
fetch_url_tool = sys.modules["agent.tools.fetch_url"]
http_request_tool = sys.modules["agent.tools.http_request"]

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_PERMANENT_REDIRECT_CODES = {301, 308}
_NO_JSON = object()


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        headers: dict[str, str] | None = None,
        text: str = "",
        json_data: object = _NO_JSON,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self.text = text
        self._json_data = json_data

    @property
    def is_redirect(self) -> bool:
        return self.status_code in _REDIRECT_CODES and "Location" in self.headers

    @property
    def is_permanent_redirect(self) -> bool:
        return self.status_code in _PERMANENT_REDIRECT_CODES and "Location" in self.headers

    def json(self) -> object:
        if self._json_data is _NO_JSON:
            raise ValueError("response is not json")
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


def test_fetch_url_blocks_private_ip_without_issuing_a_request(monkeypatch) -> None:
    def fail_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("request should not be issued for blocked URLs")

    monkeypatch.setattr(http_request_tool.requests, "request", fail_request)

    result = fetch_url_tool.fetch_url(
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
    )

    assert result["status_code"] == 0
    assert "Request blocked" in result["error"]
    assert result["url"].startswith("http://169.254.169.254/")


def test_fetch_url_blocks_redirects_to_private_ips(monkeypatch) -> None:
    calls: list[tuple[str, str, bool]] = []

    def fake_request(
        method: str, url: str, *, timeout: int, allow_redirects: bool, **kwargs
    ) -> FakeResponse:  # type: ignore[no-untyped-def]
        calls.append((method, url, allow_redirects))
        return FakeResponse(
            status_code=302,
            url=url,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )

    monkeypatch.setattr(http_request_tool.requests, "request", fake_request)

    result = fetch_url_tool.fetch_url("https://example.com/start")

    assert calls == [("GET", "https://example.com/start", False)]
    assert result["status_code"] == 0
    assert result["url"] == "http://169.254.169.254/latest/meta-data"
    assert "Request blocked" in result["error"]
