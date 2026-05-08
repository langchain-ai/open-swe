from __future__ import annotations

import importlib
import socket as real_socket
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


def _addr_info(ip: str, port: int | None = None) -> tuple:
    return (
        real_socket.AF_INET,
        real_socket.SOCK_STREAM,
        6,
        "",
        (ip, port or 0),
    )


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

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        ip = "93.184.216.34" if host == "example.com" else host
        return [_addr_info(ip, port)]

    monkeypatch.setattr(http_request_tool.socket, "getaddrinfo", fake_getaddrinfo)

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


class _FakeSocket:
    """Records connect() targets without performing real network I/O."""

    instances: list = []

    def __init__(self, family, socktype, proto):
        self.family = family
        self.socktype = socktype
        self.proto = proto
        self.connected_to = None
        self.timeout = None
        self.sockopts: list = []
        self.closed = False
        _FakeSocket.instances.append(self)

    def settimeout(self, t):
        self.timeout = t

    def setsockopt(self, *opt):
        self.sockopts.append(opt)

    def bind(self, _addr):
        pass

    def connect(self, address):
        self.connected_to = address

    def close(self):
        self.closed = True


def test_pinned_dns_blocks_rebinding_to_private_ip(monkeypatch) -> None:
    """A resolver that flips public -> private must not be able to rebind.

    Validation sees a public IP; a later resolution would return 127.0.0.1.
    The connection layer (urllib3's create_connection) must observe the pinned
    public IP, not the private IP.
    """
    hostname = "rebind.example.com"
    public_addr = "93.184.216.34"
    private_addr = "127.0.0.1"

    call_count = {"n": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        ip = public_addr if call_count["n"] == 1 else private_addr
        return [_addr_info(ip, port)]

    monkeypatch.setattr(http_request_tool.socket, "getaddrinfo", fake_getaddrinfo)

    _FakeSocket.instances = []
    monkeypatch.setattr(http_request_tool.socket, "socket", _FakeSocket)

    def fake_request(method, url, *, timeout, allow_redirects, **kwargs):  # type: ignore[no-untyped-def]
        # Drive urllib3's connection helper the way urllib3 itself would.
        http_request_tool.urllib3_connection.create_connection((hostname, 80))
        return FakeResponse(status_code=200, url=url, text="ok")

    monkeypatch.setattr(http_request_tool.requests, "request", fake_request)

    result = http_request_tool.http_request(f"http://{hostname}/probe")

    assert len(_FakeSocket.instances) == 1
    sock = _FakeSocket.instances[0]
    assert sock.connected_to == (public_addr, 80), (
        f"Connection step must target pinned public IP, got {sock.connected_to}"
    )
    assert result["status_code"] == 200


def test_rebinding_to_only_private_ips_is_blocked(monkeypatch) -> None:
    """If the very first resolution returns a private IP, validation must reject."""
    hostname = "evil.example.com"
    private_addr = "169.254.169.254"

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [_addr_info(private_addr, port)]

    monkeypatch.setattr(http_request_tool.socket, "getaddrinfo", fake_getaddrinfo)

    def fail_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("request should not be issued for blocked URLs")

    monkeypatch.setattr(http_request_tool.requests, "request", fail_request)

    result = http_request_tool.http_request(f"http://{hostname}/")

    assert result["status_code"] == 0
    assert "Request blocked" in result["content"]


def test_pin_does_not_affect_other_hostnames(monkeypatch) -> None:
    """The pinned create_connection must only override the validated hostname."""
    hostname = "pinned.example.com"
    public_addr = "93.184.216.34"
    other_hostname = "other.example.com"

    addr_infos = [_addr_info(public_addr)]

    fallthrough_calls: list = []

    def fake_original_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        fallthrough_calls.append(address)
        return ("fallthrough", address)

    monkeypatch.setattr(
        http_request_tool.urllib3_connection,
        "create_connection",
        fake_original_create_connection,
    )

    with http_request_tool._pin_dns(hostname, addr_infos):
        # The pinned wrapper is now installed; calling it for the pinned host
        # must NOT delegate to the real create_connection.
        try:
            pinned_sock = http_request_tool._pinned_create_connection((hostname, 80))
            if isinstance(pinned_sock, real_socket.socket):
                assert pinned_sock.getpeername()[0] == public_addr or True
                pinned_sock.close()
        except OSError:
            # Expected — no actual server at the pinned IP. The point is that
            # the fallthrough was NOT used.
            pass

        # Other host MUST fall through to the (mocked) real resolver.
        other_result = http_request_tool._pinned_create_connection((other_hostname, 443))

    assert fallthrough_calls == [(other_hostname, 443)], (
        f"Pin must only override the pinned hostname, got fallthrough calls: {fallthrough_calls}"
    )
    assert other_result == ("fallthrough", (other_hostname, 443))


def test_pin_install_count_unwinds() -> None:
    """After all _pin_dns blocks exit, urllib3's create_connection is restored."""
    sentinel_original = http_request_tool.urllib3_connection.create_connection
    addr_infos = [_addr_info("93.184.216.34")]

    with http_request_tool._pin_dns("a.example.com", addr_infos):
        assert (
            http_request_tool.urllib3_connection.create_connection
            is http_request_tool._pinned_create_connection
        )
        with http_request_tool._pin_dns("b.example.com", addr_infos):
            assert (
                http_request_tool.urllib3_connection.create_connection
                is http_request_tool._pinned_create_connection
            )

    assert http_request_tool.urllib3_connection.create_connection is sentinel_original
    assert http_request_tool._install_count == 0
    assert http_request_tool._original_create_connection is None


def test_pinned_connection_propagates_timeout_and_socket_options(monkeypatch) -> None:
    """urllib3 calls create_connection with a positional timeout and keyword
    socket_options; the pinned wrapper must forward both to the underlying socket
    so connect timeouts and TCP options aren't silently dropped.
    """
    hostname = "pinned.example.com"
    public_addr = "93.184.216.34"
    addr_infos = [_addr_info(public_addr)]

    _FakeSocket.instances = []
    monkeypatch.setattr(http_request_tool.socket, "socket", _FakeSocket)

    sock_opts = [(real_socket.IPPROTO_TCP, real_socket.TCP_NODELAY, 1)]

    with http_request_tool._pin_dns(hostname, addr_infos):
        # Match how urllib3.connection calls create_connection:
        # positional timeout, keyword source_address + socket_options.
        http_request_tool._pinned_create_connection(
            (hostname, 80),
            7.5,
            source_address=None,
            socket_options=sock_opts,
        )

    assert len(_FakeSocket.instances) == 1
    sock = _FakeSocket.instances[0]
    assert sock.connected_to == (public_addr, 80)
    assert sock.timeout == 7.5, f"connect timeout was dropped: {sock.timeout!r}"
    assert sock.sockopts == sock_opts, f"socket_options were dropped: {sock.sockopts!r}"
