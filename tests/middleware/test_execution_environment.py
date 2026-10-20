"""Observed capabilities reach the model without overwriting existing context."""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, SystemMessage

from agent.middleware.execution_environment import ExecutionEnvironmentMiddleware
from agent.sandboxes.environment import ExecutionEnvironment, probe_execution_environment


async def test_missing_executables_preserve_unknown_environment_and_probe_errors() -> None:
    backend = AsyncMock(spec=SandboxBackendProtocol)
    backend.aexecute.return_value = ExecuteResponse(output="executable missing", exit_code=127)
    environment = await probe_execution_environment(cast(SandboxBackendProtocol, backend))
    assert environment.shell_family == "unknown"
    assert environment.python_executable is None
    assert environment.git_version is None
    assert environment.probe_errors
    assert all("executable missing" in error for error in environment.probe_errors)


@pytest.mark.parametrize("structured", [True, False])
async def test_observed_environment_preserves_existing_system_context(structured: bool) -> None:
    model = FakeListChatModel(responses=["unused"])
    original = SystemMessage(
        content=[{"type": "text", "text": "repository context"}]
        if structured
        else "repository context"
    )
    state = {
        "messages": [],
        "execution_environment": ExecutionEnvironment(
            system="Windows", shell_family="cmd", working_dir="C:/project with spaces"
        ).model_dump(),
    }
    request = ModelRequest(model=model, messages=[], system_message=original, state=state)
    response = ModelResponse[object](result=[AIMessage(content="answer")])

    async def handler(updated: ModelRequest) -> ModelResponse[object]:
        assert updated.system_message is not None
        assert "repository context" in updated.system_message.text
        assert "C:/project with spaces" in updated.system_message.text
        if structured:
            assert isinstance(updated.system_message.content, list)
            assert updated.system_message.content[0] == original.content[0]
        return response

    middleware = ExecutionEnvironmentMiddleware(cast(SandboxBackendProtocol, AsyncMock()))
    assert await middleware.awrap_model_call(request, handler) is response
    assert request.system_message == original
