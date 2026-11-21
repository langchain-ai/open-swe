"""Which tool errors reach the model and which end the run, under MDA's tool error middleware."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langsmith.sandbox import (
    ResourceCreationError,
    ResourceNotFoundError,
    SandboxConnectionError,
    SandboxServerReloadError,
)
from managed_deepagents import RunEndingError
from managed_deepagents._tool_errors import create_tool_error_middleware  # noqa: PLC2701
from rt import tool_errors
from rt.sandbox_setup import SandboxUnavailableError
from rt.tool_errors import SandboxUnreachableError, ToolErrorMiddleware
from rt.tools import SandboxGoneError
from runtime_api.model_errors import code_for_error_type


def _request() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "execute", "args": {}, "id": "call", "type": "tool_call"},
        tool=None,
        state={"messages": []},
        runtime=MagicMock(),
    )


async def _call(error: Exception, monkeypatch: pytest.MonkeyPatch) -> tuple[object, MagicMock]:
    """Robin's middleware inside MDA's, as the agent runs them, around a tool that raises."""
    monkeypatch.setattr(tool_errors, "run_id", lambda: "run-1")
    backend = MagicMock()
    robin = ToolErrorMiddleware(backend)
    mda = create_tool_error_middleware()
    tool = AsyncMock(side_effect=error)
    result = await mda.awrap_tool_call(
        _request(), lambda request: robin.awrap_tool_call(request, tool)
    )
    return result, backend


@pytest.mark.parametrize(
    "error",
    [
        SandboxConnectionError("connection to sb-abc123 refused"),
        ResourceNotFoundError("sandbox sb-abc123 not found", resource_type="sandbox"),
    ],
)
async def test_a_sandbox_that_stopped_answering_ends_the_run(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SandboxUnreachableError) as raised:
        await _call(error, monkeypatch)

    assert raised.value.__cause__ is error
    assert str(raised.value) == str(error)


async def test_the_person_is_told_before_the_run_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_errors, "run_id", lambda: "run-1")
    backend = MagicMock()
    with pytest.raises(SandboxUnreachableError):
        await ToolErrorMiddleware(backend).awrap_tool_call(
            _request(), AsyncMock(side_effect=SandboxConnectionError("sb-abc123 refused"))
        )

    gone = backend.outbox.notify.call_args.args[1]
    assert gone.sandbox_id == "sb-abc123"


async def test_a_sandbox_that_never_booted_ends_the_run_unwrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = SandboxUnavailableError("Invalid api_key")

    with pytest.raises(SandboxUnavailableError) as raised:
        await _call(error, monkeypatch)

    assert raised.value is error


@pytest.mark.parametrize(
    "error",
    [
        SandboxServerReloadError("the sandbox server reloaded"),
        ResourceNotFoundError("no such file", resource_type="file"),
        ValueError("bad argument"),
    ],
)
async def test_other_tool_errors_reach_the_model(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, backend = await _call(error, monkeypatch)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert json.loads(result.text)["error_type"] == type(error).__name__
    backend.outbox.notify.assert_not_called()


async def test_an_error_robin_does_not_handle_reaches_the_model_through_mda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool Robin's middleware does not wrap: MDA returns its error to the model."""
    mda = create_tool_error_middleware()

    result = await mda.awrap_tool_call(_request(), AsyncMock(side_effect=KeyError("missing")))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "KeyError" in result.text


def test_robins_run_ending_errors_are_mdas() -> None:
    for error in (SandboxUnreachableError, SandboxUnavailableError, SandboxGoneError):
        assert issubclass(error, RunEndingError)


@pytest.mark.parametrize(
    ("error", "ends_as", "code"),
    [
        (
            SandboxConnectionError("sb-abc123 refused"),
            "SandboxUnreachableError",
            "sandbox_unreachable",
        ),
        (
            ResourceCreationError("Image pull failed", resource_type="sandbox"),
            "SandboxUnavailableError",
            None,
        ),
    ],
)
def test_a_sandbox_that_cannot_boot_for_a_tool_call_ends_the_run_with_its_reason(
    error: Exception, ends_as: str, code: str | None
) -> None:
    """The backend's failure reply reads only the class name the run ended with."""
    from rt.managed_sandbox import _unbooted  # noqa: PLC0415, PLC2701

    ended = _unbooted(error)

    assert isinstance(ended, RunEndingError)
    assert type(ended).__name__ == ends_as
    assert code_for_error_type(ends_as) == code
