"""A proxy config write the sandbox API refuses."""

import pytest


def test_a_refused_write_says_why_without_its_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sandbox API's reason for refusing a proxy config write (HTTP 422) reaches the run's
    error, so a refusal says which part it rejected, with any credential it echoes removed."""
    import httpx
    from managed_deepagents import _sandbox_proxy
    from managed_deepagents import runtime as mda_runtime
    from managed_deepagents.sandbox import SandboxDefinition

    detail = "proxy_config.rules[2]: cloud identity rule cannot change; header ghs_abc123secret"

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": detail})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(refuse))
    )
    monkeypatch.setattr(mda_runtime, "_sandbox_client_kwargs", lambda sandbox: {"api_key": "k"})
    monkeypatch.setattr(
        mda_runtime, "_configured_sandbox_api_endpoint", lambda: "https://ls.invalid"
    )

    with pytest.raises(_sandbox_proxy.ProxyConfigRefusedError) as refused:
        _sandbox_proxy.patch_sandbox_proxy_config(SandboxDefinition({}), "sandbox-1", {"rules": []})

    message = str(refused.value)
    assert "HTTP 422" in message
    assert "cloud identity rule cannot change" in message
    assert "ghs_abc123secret" not in message and "ghs_[redacted]" in message
