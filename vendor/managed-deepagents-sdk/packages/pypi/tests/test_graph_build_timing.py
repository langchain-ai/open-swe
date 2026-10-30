"""Tests for the per-phase managed graph build log line."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from langchain_core.tools import tool

from managed_deepagents import define_mcp
from managed_deepagents import runtime as runtime_mod
from managed_deepagents._connector import Connector
from managed_deepagents._connectors.mcp import load as mcp_load
from managed_deepagents._graph_build_timing import GraphBuildTiming, build_step
from managed_deepagents.define_deep_agent import DeepAgentDefinition, define_deep_agent

if TYPE_CHECKING:
    from tests.conftest import CapturedLogs

_BUILD_LINE = "graph build took"


class _Clock:
    """Seconds that advance only when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_build_logs_total_and_each_phase_in_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock()
    timing = GraphBuildTiming(clock)
    with timing.phase("factory"):
        clock.now += 0.098
    with timing.phase("instructions"):
        clock.now += 0.6404
    clock.now += 0.012

    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="run", run_id="run-1")

    assert caplog.messages == [
        "[mda] graph build took 750ms (kind=run, run_id=run-1): factory=98ms instructions=640ms"
    ]


def test_fast_build_still_logs(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    timing = GraphBuildTiming(clock)
    with timing.phase("factory"):
        clock.now += 0.002

    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="read", run_id=None)

    assert caplog.messages == ["[mda] graph build took 2ms (kind=read): factory=2ms"]


def test_repeated_phase_accumulates(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    timing = GraphBuildTiming(clock)
    for _ in range(2):
        with timing.phase("context"):
            clock.now += 0.06

    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="read", run_id=None)

    assert caplog.messages == ["[mda] graph build took 120ms (kind=read): context=120ms"]


def test_measure_returns_the_result_and_records_its_phase(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock()
    timing = GraphBuildTiming(clock)

    def build() -> str:
        clock.now += 0.03
        return "built"

    assert timing.measure("create_deep_agent", build) == "built"
    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="run", run_id=None)

    assert caplog.messages == ["[mda] graph build took 30ms (kind=run): create_deep_agent=30ms"]


def test_steps_inside_a_phase_follow_its_time(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    timing = GraphBuildTiming(clock)
    with timing.phase("connectors"):
        with build_step("github"):
            clock.now += 0.3
        with build_step("linear"):
            clock.now += 0.22

    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="run", run_id=None)

    assert caplog.messages == [
        "[mda] graph build took 520ms (kind=run): connectors=520ms (github=300ms linear=220ms)"
    ]


def test_step_outside_a_build_records_nothing(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    timing = GraphBuildTiming(clock)
    with build_step("github"):
        clock.now += 0.3

    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="run", run_id=None)

    assert caplog.messages == ["[mda] graph build took 300ms (kind=run)"]


def test_sizes_follow_the_phases(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    timing = GraphBuildTiming(clock)
    with timing.phase("create_deep_agent"):
        clock.now += 0.03
    timing.set_sizes(tools=143, middleware=12, subagents=4)

    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="run", run_id=None)

    assert caplog.messages == [
        (
            "[mda] graph build took 30ms (kind=run): create_deep_agent=30ms"
            " tools=143 middleware=12 subagents=4"
        )
    ]


def test_run_id_is_sanitized_before_it_reaches_the_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    timing = GraphBuildTiming(_Clock())

    with caplog.at_level(logging.INFO, logger="mda"):
        timing.log(kind="run", run_id="run\nforged")

    assert caplog.messages == ["[mda] graph build took 0ms (kind=run, run_id=run?forged)"]


def test_build_sends_numeric_phases_to_the_log_sink(captured_logs: CapturedLogs) -> None:
    from managed_deepagents._telemetry.otel import (  # noqa: PLC0415
        LogContext,
        enter_log_context,
        exit_log_context,
    )

    clock = _Clock()
    timing = GraphBuildTiming(clock)
    with timing.phase("create_deep_agent"):
        clock.now += 0.25

    token = enter_log_context(LogContext(trace_id="a" * 32))
    try:
        timing.log(kind="run", run_id="run-1")
    finally:
        exit_log_context(token)

    [record] = captured_logs.records
    assert record.attributes == {
        "mda.graph_build.kind": "run",
        "mda.graph_build.total_ms": 250,
        "mda.graph_build.create_deep_agent_ms": 250,
    }


@pytest.fixture
def fake_create_deep_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_mod, "create_deep_agent", lambda **_kwargs: object())


def _build_line(messages: list[str]) -> str:
    [line] = [message for message in messages if _BUILD_LINE in message]
    return line


def _build_phases(messages: list[str]) -> tuple[str, list[str]]:
    header, _, body = _build_line(messages).partition(": ")
    tokens = re.sub(r" \([^)]*\)", "", body).split()
    return header, [token.split("=")[0] for token in tokens if token.endswith("ms")]


def _build_sizes(messages: list[str]) -> dict[str, int]:
    _, _, body = _build_line(messages).partition(": ")
    pairs = (token.split("=") for token in body.split() if not token.endswith("ms"))
    return {name: int(value) for name, value in pairs}


@pytest.mark.usefixtures("fake_create_deep_agent")
def test_factory_run_logs_each_managed_phase(caplog: pytest.LogCaptureFixture) -> None:
    def build(_runtime: object) -> DeepAgentDefinition:
        return define_deep_agent(name="timed", instructions="Be brief.")

    with caplog.at_level(logging.INFO, logger="mda"):
        asyncio.run(
            runtime_mod.compile_managed_agent_async(
                build,
                {"metadata": {"run_id": "run-42"}},
                run_context={},
                is_run=True,
                report_telemetry=False,
            )
        )

    header, phases = _build_phases(caplog.messages)
    assert header.endswith("(kind=run, run_id=run-42)")
    assert phases == [
        "factory",
        "context",
        "skills",
        "instructions",
        "create_deep_agent",
    ]


@pytest.mark.usefixtures("fake_create_deep_agent")
def test_inspection_logs_as_a_read(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="mda"):
        asyncio.run(
            runtime_mod.compile_managed_agent_async(
                define_deep_agent(name="static"), is_run=False, report_telemetry=False
            )
        )

    header, phases = _build_phases(caplog.messages)
    assert header.endswith("(kind=read)")
    assert phases == ["factory", "skills", "instructions", "create_deep_agent"]


@pytest.mark.usefixtures("fake_create_deep_agent")
def test_connector_tools_are_their_own_phase(caplog: pytest.LogCaptureFixture) -> None:
    class ToolConnector(Connector):
        kind = "test"

        def tools(self, _context: object) -> list[object]:
            return []

    with caplog.at_level(logging.INFO, logger="mda"):
        asyncio.run(
            runtime_mod.compile_managed_agent_async(
                define_deep_agent(name="tools"),
                run_context={},
                connectors=[ToolConnector()],
                report_telemetry=False,
            )
        )

    _, phases = _build_phases(caplog.messages)
    assert "connectors" in phases


@pytest.mark.usefixtures("fake_create_deep_agent")
def test_factory_eval_trial_logs_its_build(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    def build(_runtime: object) -> DeepAgentDefinition:
        return define_deep_agent(name="trial")

    with caplog.at_level(logging.INFO, logger="mda"):
        asyncio.run(
            runtime_mod.compile_agent_factory_for_eval_async(
                build, {"configurable": {"cwd": str(tmp_path)}}
            )
        )

    header, phases = _build_phases(caplog.messages)
    assert header.endswith("(kind=run)")
    assert phases[0] == "factory"


@pytest.mark.usefixtures("fake_create_deep_agent")
def test_build_line_counts_tools_middleware_and_subagents(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    helper = {"name": "helper", "description": "Helps.", "system_prompt": "Help."}
    with caplog.at_level(logging.INFO, logger="mda"):
        asyncio.run(
            runtime_mod.compile_managed_agent_async(
                define_deep_agent(name="sized", tools=[add], subagents=[helper]),
                run_context={},
                report_telemetry=False,
            )
        )

    sizes = _build_sizes(caplog.messages)
    assert sizes["tools"] == 1
    assert sizes["subagents"] == 1
    assert sizes["middleware"] >= 1


@pytest.mark.usefixtures("fake_create_deep_agent")
def test_each_mcp_server_gets_its_own_connector_time(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def discover(_name: str, _client_factory: object) -> list[object]:
        return []

    monkeypatch.setattr(mcp_load, "_discover_tools", discover)
    mcp = define_mcp(
        servers={
            "github": {"transport": "http", "url": "https://github.example/mcp"},
            "linear": {"transport": "http", "url": "https://linear.example/mcp"},
        }
    )

    with caplog.at_level(logging.INFO, logger="mda"):
        asyncio.run(
            runtime_mod.compile_managed_agent_async(
                define_deep_agent(name="mcp"),
                run_context={},
                connectors=[mcp],
                report_telemetry=False,
            )
        )

    assert re.search(
        r" connectors=\d+ms \(github=\d+ms linear=\d+ms\) ", _build_line(caplog.messages)
    )
