"""The proxy config MDA writes, as the backend hands it over, and when it asks again."""

from datetime import UTC, datetime, timedelta

import pytest
from rt.managed_sandbox import SandboxProxyWriter


def _holding() -> SandboxProxyWriter:
    """A writer that holds the config the backend handed over."""
    writer = SandboxProxyWriter()
    writer._backend_config = {"rules": [{"name": "github-api"}, {"name": "open-swe-thread-tools"}]}
    return writer


def test_the_config_is_written_as_the_backend_handed_it_over() -> None:
    assert _holding().config() == {
        "rules": [{"name": "github-api"}, {"name": "open-swe-thread-tools"}]
    }
    assert SandboxProxyWriter().config() is None


@pytest.mark.parametrize(
    ("minutes_left", "stale"), [(None, False), (60, False), (16, False), (14, True), (-1, True)]
)
def test_the_config_is_asked_for_again_before_its_token_expires(
    minutes_left: int | None, stale: bool
) -> None:
    writer = _holding()
    writer._backend_expires_at = (
        None if minutes_left is None else datetime.now(UTC) + timedelta(minutes=minutes_left)
    )

    assert writer._stale() is stale


async def test_rewriting_after_a_refusal_asks_for_a_newly_minted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _holding()
    asked: list[bool] = []

    async def write(runtime: object, sandbox_id: str | None = None) -> None:
        asked.append(writer._force_refresh)
        writer._backend_config = {"rules": []}
        writer._backend_expires_at = datetime.now(UTC) + timedelta(hours=1)

    monkeypatch.setattr(writer, "write", write)

    assert await writer.rewrite_fresh(object()) is True
    assert asked == [True]
    assert writer._force_refresh is False


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
