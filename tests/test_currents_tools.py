from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.integrations import currents_tools


@pytest.mark.asyncio
async def test_load_currents_tools_empty_when_not_connected() -> None:
    with patch.object(currents_tools, "get_currents_api_key", AsyncMock(return_value=None)):
        assert await currents_tools.load_currents_tools("alice") == []


@pytest.mark.asyncio
async def test_load_currents_tools_names() -> None:
    with patch.object(currents_tools, "get_currents_api_key", AsyncMock(return_value="k")):
        tools = await currents_tools.load_currents_tools("alice")
    assert {t.name for t in tools} == {
        "currents_list_projects",
        "currents_get_run",
        "currents_find_run",
        "currents_list_project_runs",
        "currents_get_instance",
    }


@pytest.mark.asyncio
async def test_currents_get_run_success() -> None:
    payload = {"status": "OK", "data": {"runId": "run_123", "status": "failed"}}
    with patch.object(currents_tools, "_get", AsyncMock(return_value=payload)):
        tools = currents_tools._make_tools("test-key")
        get_run = next(t for t in tools if t.name == "currents_get_run")
        result = await get_run.ainvoke({"run_id": "run_123"})
    assert result == payload


@pytest.mark.asyncio
async def test_currents_get_run_error() -> None:
    with patch.object(currents_tools, "_get", AsyncMock(side_effect=RuntimeError("boom"))):
        tools = currents_tools._make_tools("bad-key")
        get_run = next(t for t in tools if t.name == "currents_get_run")
        result = await get_run.ainvoke({"run_id": "run_123"})
    assert result["success"] is False
    assert "boom" in result["error"]


@pytest.mark.asyncio
async def test_currents_list_projects_caps_limit() -> None:
    captured: dict[str, object] = {}

    async def fake_get(path: str, api_key: str, **params):
        captured["path"] = path
        captured["limit"] = params.get("limit")
        return {"status": "OK", "data": []}

    with patch.object(currents_tools, "_get", side_effect=fake_get):
        tools = currents_tools._make_tools("k")
        list_projects = next(t for t in tools if t.name == "currents_list_projects")
        result = await list_projects.ainvoke({"limit": 9999})
    assert result["status"] == "OK"
    assert captured["limit"] == 50
    assert captured["path"] == "/projects"


@pytest.mark.asyncio
async def test_currents_find_run_passes_params() -> None:
    captured: dict[str, object] = {}

    async def fake_get(path: str, api_key: str, **params):
        captured["path"] = path
        captured.update(params)
        return {"status": "OK", "data": {"runId": "r1"}}

    with patch.object(currents_tools, "_get", side_effect=fake_get):
        tools = currents_tools._make_tools("k")
        find_run = next(t for t in tools if t.name == "currents_find_run")
        result = await find_run.ainvoke(
            {"project_id": "proj_1", "ci_build_id": "build-42", "branch": "main"}
        )
    assert result["status"] == "OK"
    assert captured["path"] == "/runs/find"
    assert captured["projectId"] == "proj_1"
    assert captured["ciBuildId"] == "build-42"
    assert captured["branch"] == "main"


@pytest.mark.asyncio
async def test_currents_list_project_runs_caps_limit() -> None:
    captured: dict[str, object] = {}

    async def fake_get(path: str, api_key: str, **params):
        captured["path"] = path
        captured.update(params)
        return {"status": "OK", "data": []}

    with patch.object(currents_tools, "_get", side_effect=fake_get):
        tools = currents_tools._make_tools("k")
        list_runs = next(t for t in tools if t.name == "currents_list_project_runs")
        await list_runs.ainvoke(
            {
                "project_id": "proj_1",
                "limit": 9999,
                "status": "FAILED",
                "branch": "main",
                "starting_after": "cursor-abc",
            }
        )
    assert captured["path"] == "/projects/proj_1/runs"
    assert captured["limit"] == 50
    assert captured["status"] == "FAILED"
    assert captured["branches[]"] == "main"
    assert captured["starting_after"] == "cursor-abc"


@pytest.mark.asyncio
async def test_currents_get_instance() -> None:
    payload = {"status": "OK", "data": {"instanceId": "inst_1"}}
    with patch.object(currents_tools, "_get", AsyncMock(return_value=payload)):
        tools = currents_tools._make_tools("k")
        get_instance = next(t for t in tools if t.name == "currents_get_instance")
        result = await get_instance.ainvoke({"instance_id": "inst_1"})
    assert result == payload
