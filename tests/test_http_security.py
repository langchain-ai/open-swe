from __future__ import annotations

import importlib
import socket
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


# --- DNS rebinding (issue #1186) -------------------------------------------------

_PUBLIC_IP = "8.8.8.8"  # Google DNS — globally routable, passes ip_address.is_private
_PUBLIC_IP_2 = "1.1.1.1"  # Cloudflare DNS
_PRIVATE_IP = "192.168.1.1"


def _addr_info(ip: str, port: int) -> tuple:
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))


def test_pinned_dns_resolution_returns_pinned_ips_for_target_host(monkeypatch) -> None:
    def fail_original(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("system resolver should not be hit for the pinned host")

    monkeypatch.setattr(socket, "getaddrinfo", fail_original)

    with http_request_tool._pinned_dns_resolution("api.example.com", [_PUBLIC_IP]):
        result = socket.getaddrinfo("api.example.com", 443)

    assert result == [_addr_info(_PUBLIC_IP, 443)]


def test_pinned_dns_resolution_falls_through_for_unrelated_hosts(monkeypatch) -> None:
    seen: list[tuple[str, int]] = []

    def fake_original(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((host, port))
        return [_addr_info(_PUBLIC_IP_2, port)]

    monkeypatch.setattr(socket, "getaddrinfo", fake_original)

    with http_request_tool._pinned_dns_resolution("api.example.com", [_PUBLIC_IP]):
        result = socket.getaddrinfo("other-host.example", 80)

    assert seen == [("other-host.example", 80)]
    assert result == [_addr_info(_PUBLIC_IP_2, 80)]


def test_pinned_dns_resolution_restores_original_after_exit(monkeypatch) -> None:
    sentinel_calls: list[tuple] = []

    def fake_original(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        sentinel_calls.append((host, port))
        return [_addr_info(_PUBLIC_IP_2, port)]

    monkeypatch.setattr(socket, "getaddrinfo", fake_original)

    with http_request_tool._pinned_dns_resolution("api.example.com", [_PUBLIC_IP]):
        pass

    socket.getaddrinfo("api.example.com", 443)
    assert sentinel_calls == [("api.example.com", 443)]


def test_pinned_dns_resolution_supports_ipv6() -> None:
    with http_request_tool._pinned_dns_resolution("api.example.com", ["2001:db8::1"]):
        result = socket.getaddrinfo("api.example.com", 443)

    assert result == [
        (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("2001:db8::1", 443, 0, 0),
        )
    ]


def test_dns_rebinding_attack_uses_pinned_ip_during_connect(monkeypatch) -> None:
    """Validation sees a public IP; an attacker tries to rebind to a private IP
    before the connect step runs. With pinning, the connect-time lookup must
    return the IPs the validator already approved — not the rebound value."""

    lookup_log: list[str] = []

    def rebinding_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        lookup_log.append(host)
        # First call (validation in _is_url_safe) — return public IP.
        # Subsequent calls — simulate the attacker swapping to a private record.
        if len([h for h in lookup_log if h == "rebind.example"]) == 1:
            return [_addr_info(_PUBLIC_IP, port or 0)]
        return [_addr_info(_PRIVATE_IP, port or 0)]

    captured_resolutions: list[str] = []

    def fake_request(method, url, **kwargs):  # type: ignore[no-untyped-def]
        # Re-resolve from inside the request scope. With the pin in effect,
        # this must return the pinned (public) IP — not the rebound private one.
        result = socket.getaddrinfo("rebind.example", 443)
        captured_resolutions.append(result[0][4][0])
        return FakeResponse(status_code=200, url=url, text='{"ok": true}', json_data={"ok": True})

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    monkeypatch.setattr(http_request_tool.requests, "request", fake_request)

    result = http_request_tool.http_request("https://rebind.example/data")

    assert result["status_code"] == 200
    # The pin must have intercepted the connect-time lookup and returned the
    # validator-approved public IP, not the attacker's private rebind.
    assert captured_resolutions == [_PUBLIC_IP]


def test_redirect_repins_to_new_target_host(monkeypatch) -> None:
    """Each redirect target gets its own validation + pin — a redirect to a fresh
    hostname must use the new hostname's validated IPs, not the previous host's."""

    lookup_log: list[tuple[str, int]] = []
    sequence = iter(
        [
            # _is_url_safe("https://first.example/start")
            [_addr_info(_PUBLIC_IP, 0)],
            # _is_url_safe("https://second.example/end")
            [_addr_info(_PUBLIC_IP_2, 0)],
        ]
    )

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        lookup_log.append((host, port or 0))
        try:
            return next(sequence)
        except StopIteration:
            return [_addr_info(_PUBLIC_IP_2, port or 0)]

    seen_pinned: list[tuple[str, str]] = []

    def fake_request(method, url, **kwargs):  # type: ignore[no-untyped-def]
        host = url.split("//", 1)[1].split("/", 1)[0]
        resolved = socket.getaddrinfo(host, 443)[0][4][0]
        seen_pinned.append((host, resolved))
        if host == "first.example":
            return FakeResponse(
                status_code=302,
                url=url,
                headers={"Location": "https://second.example/end"},
            )
        return FakeResponse(status_code=200, url=url, text="ok")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(http_request_tool.requests, "request", fake_request)

    result = http_request_tool.http_request("https://first.example/start")

    assert result["status_code"] == 200
    assert seen_pinned == [
        ("first.example", _PUBLIC_IP),
        ("second.example", _PUBLIC_IP_2),
    ]


def test_is_url_safe_returns_validated_ips(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [_addr_info(_PUBLIC_IP, 0)])
    is_safe, reason, ips = http_request_tool._is_url_safe("https://example.com/data")
    assert is_safe is True
    assert reason == ""
    assert ips == [_PUBLIC_IP]


def test_is_url_safe_blocks_when_any_resolved_ip_is_private(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [_addr_info(_PUBLIC_IP, 0), _addr_info(_PRIVATE_IP, 0)],
    )
    is_safe, reason, ips = http_request_tool._is_url_safe("https://mixed.example/")
    assert is_safe is False
    assert _PRIVATE_IP in reason
    assert ips == []


def test_is_url_safe_dedupes_validated_ips(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [_addr_info(_PUBLIC_IP, 0), _addr_info(_PUBLIC_IP, 0)],
    )
    is_safe, _reason, ips = http_request_tool._is_url_safe("https://dupe.example/")
    assert is_safe is True
    assert ips == [_PUBLIC_IP]
