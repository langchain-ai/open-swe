import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.local_agent import _model_pair, get_local_agent, resolve_local_project_path
from agent.local_auth import authenticate


def test_resolve_local_project_path_rereads_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    allowlist = tmp_path / "projects.json"
    allowlist.write_text(json.dumps([{"cwd": str(first)}]))
    monkeypatch.setenv("OPEN_SWE_LOCAL_PROJECTS_FILE", str(allowlist))

    assert resolve_local_project_path({"local_project_path": str(first)}) == os.path.realpath(first)
    allowlist.write_text(json.dumps([{"cwd": str(second)}]))
    with pytest.raises(ValueError, match="not an allowed"):
        resolve_local_project_path({"local_project_path": str(first)})


def test_model_and_effort_come_from_configurable() -> None:
    assert _model_pair({"model": "openai:gpt-5.6-sol", "effort": "high"}) == (
        "openai:gpt-5.6-sol",
        "high",
    )


@pytest.mark.asyncio
async def test_local_auth_requires_launch_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_LOCAL_AUTH_TOKEN", "secret")
    assert await authenticate("Bearer secret") == {"identity": "local-user"}
    for authorization in ("Bearer wrong", "Basic secret", None):
        with pytest.raises(Exception) as exc_info:
            await authenticate(authorization)
        assert getattr(exc_info.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_graph_load_does_not_read_allowlist() -> None:
    with patch("agent.local_agent.create_deep_agent") as create:
        create.return_value.with_config.return_value = object()
        await get_local_agent({"configurable": {}})
    create.assert_called_once_with(system_prompt="", tools=[])
