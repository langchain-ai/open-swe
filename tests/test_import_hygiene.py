"""Guardrails against import-graph regressions.

Slow imports of agent.webapp delay pod readiness on LangGraph Cloud and have
caused runs to fail with "exceeded max attempts". These tests pin which heavy
modules are allowed in each entrypoint's transitive import closure.
"""

import json
import subprocess
import sys


def _closure_check(entry: str, forbidden: list[str]) -> dict[str, bool]:
    code = (
        "import importlib, json, sys; "
        f"importlib.import_module({entry!r}); "
        f"print(json.dumps({{m: (m in sys.modules) for m in {forbidden!r}}}))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_webapp_does_not_import_agent_stack() -> None:
    loaded = _closure_check(
        "agent.webapp",
        [
            "deepagents",
            "anthropic",
            "langchain_anthropic",
            "openai",
            "exa_py",
            "agent.server",
            "agent.middleware",
            "agent.tools",
        ],
    )
    assert not any(loaded.values()), f"forbidden modules imported by agent.webapp: {loaded}"


def test_server_does_not_import_exa_or_dashboard_routes() -> None:
    loaded = _closure_check("agent.server", ["exa_py"])
    assert not any(loaded.values()), f"forbidden modules imported by agent.server: {loaded}"


def test_lazy_names_all_resolve() -> None:
    code = (
        "import agent.tools, agent.middleware, agent.dashboard;"
        "[getattr(agent.tools, n) for n in agent.tools.__all__];"
        "[getattr(agent.middleware, n) for n in agent.middleware.__all__];"
        "agent.dashboard.router"
    )
    subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
