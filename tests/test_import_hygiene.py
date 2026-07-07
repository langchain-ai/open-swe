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
    loaded = _closure_check("agent.server", ["exa_py", "agent.dashboard.routes", "agent.webapp"])
    assert not any(loaded.values()), f"forbidden modules imported by agent.server: {loaded}"


def test_lazy_names_all_resolve() -> None:
    code = """
import importlib
import types

for package_name in ("agent.tools", "agent.middleware"):
    package = importlib.import_module(package_name)
    for name in package.__all__:
        namespace = {}
        exec(f"from {package_name} import {name} as value", namespace)
        if isinstance(namespace["value"], types.ModuleType):
            raise AssertionError(f"{package_name}.{name} resolved to a module")

namespace = {}
exec("from agent.dashboard import router as value", namespace)
if isinstance(namespace["value"], types.ModuleType):
    raise AssertionError("agent.dashboard.router resolved to a module")
"""
    subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
