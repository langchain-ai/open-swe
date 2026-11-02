"""Check a channel endpoint's answer as MDA does, and stub the MDA runtime it signs runs for."""

from typing import cast

import pytest
from managed_deepagents._channels.http.endpoint import EndpointDeclined, _run_result
from managed_deepagents._channels.run_input import NormalizedRunInput, normalize_run_input
from pydantic import JsonValue

from openswe.agent_api.tokens import RemoteRun, sign_runtime_token
from openswe.threads import runtimes


def mda_starts(answer: object) -> NormalizedRunInput:
    """The run MDA starts for an endpoint's answer; raises as MDA does when it starts none."""
    normalized = normalize_run_input(_run_result(cast(JsonValue, answer)))
    assert normalized is not None
    return normalized


def mda_declines(answer: object) -> str:
    """What MDA's Linear channel tells the session for an endpoint's decline."""
    declined = _run_result(cast(JsonValue, answer))
    assert isinstance(declined, EndpointDeclined)
    return declined.message


def stub_mda_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sign run tokens without MDA: the context a run starts with names its graph and token."""

    async def context(
        configurable: dict[str, JsonValue],
        *,
        thread_id: str,
        assistant_id: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        run = RemoteRun(
            thread_id=thread_id,
            assistant_id=assistant_id,
            configurable=configurable,
            metadata=metadata or {},
        )
        return {"graph": assistant_id, "__run_token": sign_runtime_token(run)}

    monkeypatch.setattr(runtimes, "remote_run_context", context)
    monkeypatch.setattr(runtimes, "get_tracer_project", lambda: "open-swe-test")
    monkeypatch.setattr(runtimes.RUNTIMES, "_mda", runtimes.MdaRuntime("https://mda.test"))
    monkeypatch.setattr(runtimes.Runtimes, "mda_runtime", property(lambda self: self._mda))
