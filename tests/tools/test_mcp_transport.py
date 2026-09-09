import socket

import httpx
import pytest

from agent.tool_loaders import mcp_transport


async def test_public_address_is_pinned_and_tls_hostname_preserved(monkeypatch):
    monkeypatch.setattr(
        mcp_transport,
        "resolve_and_validate",
        lambda url: (
            True,
            "",
            "example.com",
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        ),
    )
    seen = []

    async def remote(request):
        seen.append(request)
        return httpx.Response(200)

    transport = mcp_transport.MCPTransport("https://example.com/mcp")
    await transport._transport.aclose()
    transport._transport = httpx.MockTransport(remote)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post("https://example.com/mcp", headers={"Authorization": "test-key"})
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["Host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"


@pytest.mark.parametrize("url", ["http://example.com/mcp", "https://other.example/mcp"])
async def test_server_cannot_redirect_credentials_to_another_origin(url):
    async with mcp_transport.mcp_http_client("https://example.com/mcp") as client:
        with pytest.raises(ValueError, match="configured HTTPS origin"):
            await client.get(url)
        assert client.follow_redirects is False


async def test_private_or_unresolved_address_is_blocked(monkeypatch):
    monkeypatch.setattr(
        mcp_transport, "resolve_and_validate", lambda url: (False, "blocked", "example.com", None)
    )
    async with mcp_transport.mcp_http_client("https://example.com/mcp") as client:
        with pytest.raises(ValueError, match="public addresses"):
            await client.get("https://example.com/mcp")


async def test_connection_failure_tries_next_validated_address(monkeypatch):
    addresses = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]
    monkeypatch.setattr(
        mcp_transport, "resolve_and_validate", lambda url: (True, "", "example.com", addresses)
    )
    seen = []

    async def remote(request):
        seen.append(request.url.host)
        if len(seen) == 1:
            raise httpx.ConnectError("IPv6 unavailable")
        return httpx.Response(200)

    transport = mcp_transport.MCPTransport("https://example.com/mcp")
    await transport._transport.aclose()
    transport._transport = httpx.MockTransport(remote)
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get("https://example.com/mcp")).status_code == 200
    assert seen == ["2606:4700:4700::1111", "93.184.216.34"]
