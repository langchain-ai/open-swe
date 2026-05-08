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

    def fake_real_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        ip = "93.184.216.34" if host == "example.com" else host
        return [
            (
                http_request_tool.socket.AF_INET,
                http_request_tool.socket.SOCK_STREAM,
                6,
                "",
                (ip, port or 0),
            )
        ]

    monkeypatch.setattr(http_request_tool, "_real_getaddrinfo", fake_real_getaddrinfo)

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


def test_pinned_dns_blocks_rebinding_to_private_ip(monkeypatch) -> None:
    """A resolver that flips from public to private must not be able to rebind.

    Simulates a controlled DNS resolver that returns a public IP at validation
    time and a private IP on every subsequent call. With the DNS pin in place,
    the connection step (which calls socket.getaddrinfo again) must observe the
    pinned public IP, not the private IP.
    """
    hostname = "rebind.example.com"
    public_addr = "93.184.216.34"
    private_addr = "127.0.0.1"

    call_count = {"n": 0}

    def fake_real_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        ip = public_addr if call_count["n"] == 1 else private_addr
        return [
            (
                http_request_tool.socket.AF_INET,
                http_request_tool.socket.SOCK_STREAM,
                6,
                "",
                (ip, port or 0),
            )
        ]

    monkeypatch.setattr(http_request_tool, "_real_getaddrinfo", fake_real_getaddrinfo)

    observed_addresses: list[str] = []

    def fake_request(method, url, *, timeout, allow_redirects, **kwargs):  # type: ignore[no-untyped-def]
        # Simulate what urllib3 would do: resolve again right before connecting.
        infos = http_request_tool.socket.getaddrinfo(hostname, 80)
        observed_addresses.append(infos[0][4][0])
        return FakeResponse(status_code=200, url=url, text="ok")

    monkeypatch.setattr(http_request_tool.requests, "request", fake_request)

    result = http_request_tool.http_request(f"http://{hostname}/probe")

    assert observed_addresses == [public_addr], (
        f"Connection step must see pinned public IP, got {observed_addresses}"
    )
    assert result["status_code"] == 200


def test_rebinding_to_only_private_ips_is_blocked(monkeypatch) -> None:
    """If the very first resolution returns a private IP, validation must reject."""
    hostname = "evil.example.com"
    private_addr = "169.254.169.254"

    def fake_real_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [
            (
                http_request_tool.socket.AF_INET,
                http_request_tool.socket.SOCK_STREAM,
                6,
                "",
                (private_addr, port or 0),
            )
        ]

    monkeypatch.setattr(http_request_tool, "_real_getaddrinfo", fake_real_getaddrinfo)

    def fail_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("request should not be issued for blocked URLs")

    monkeypatch.setattr(http_request_tool.requests, "request", fail_request)

    result = http_request_tool.http_request(f"http://{hostname}/")

    assert result["status_code"] == 0
    assert "Request blocked" in result["content"]


def test_pin_does_not_affect_other_hostnames(monkeypatch) -> None:
    """The DNS pin must only override the validated hostname, not unrelated ones."""
    hostname = "pinned.example.com"
    public_addr = "93.184.216.34"
    other_hostname = "other.example.com"
    other_addr = "8.8.8.8"

    def fake_real_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host == hostname:
            return [
                (
                    http_request_tool.socket.AF_INET,
                    http_request_tool.socket.SOCK_STREAM,
                    6,
                    "",
                    (public_addr, port or 0),
                )
            ]
        return [
            (
                http_request_tool.socket.AF_INET,
                http_request_tool.socket.SOCK_STREAM,
                6,
                "",
                (other_addr, port or 0),
            )
        ]

    monkeypatch.setattr(http_request_tool, "_real_getaddrinfo", fake_real_getaddrinfo)

    addr_infos = fake_real_getaddrinfo(hostname, None)
    with http_request_tool._pin_dns(hostname, addr_infos):
        pinned = http_request_tool.socket.getaddrinfo(hostname, 80)
        other = http_request_tool.socket.getaddrinfo(other_hostname, 80)

    assert pinned[0][4][0] == public_addr
    assert other[0][4][0] == other_addr
