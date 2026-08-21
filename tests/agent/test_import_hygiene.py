"""Guardrails against import-graph regressions.

Slow imports of agent.webapp delay pod readiness on LangGraph Cloud and have
caused runs to fail with "exceeded max attempts". These tests pin which heavy
modules are allowed in each entrypoint's transitive import closure, and which
layers may depend on which.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent"


def _imported_packages(module_path: Path) -> set[str]:
    """Every ``agent.*`` module named by an import in ``module_path``.

    Absolute and relative, module-level and function-local alike — a lazy
    import inside a function is still a dependency between the two layers.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    package = ["agent", *module_path.relative_to(_AGENT_ROOT).parts[:-1]]
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(package[: len(package) - (node.level - 1)])
                imported.add(f"{base}.{node.module}" if node.module else base)
            elif node.module:
                imported.add(node.module)
    return {name for name in imported if name.startswith("agent.")}


def _modules_importing(package: str, forbidden_prefix: str) -> dict[str, set[str]]:
    offenders: dict[str, set[str]] = {}
    for path in sorted((_AGENT_ROOT / package).rglob("*.py")):
        hits = {
            name
            for name in _imported_packages(path)
            if name == forbidden_prefix or name.startswith(f"{forbidden_prefix}.")
        }
        if hits:
            offenders[str(path.relative_to(_AGENT_ROOT))] = hits
    return offenders


def test_dashboard_does_not_import_the_webhook_layer() -> None:
    """Webhooks adapt inbound events onto domain modules; nothing depends back on them."""
    assert _modules_importing("dashboard", "agent.webhooks") == {}


def test_tools_do_not_import_the_webhook_layer() -> None:
    """Agent tools call the domain (``agent.review.dispatch``), never the webhook adapter."""
    assert _modules_importing("tools", "agent.webhooks") == {}


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
            "agent.graphs.agent",
            "agent.middleware",
            "agent.tools",
        ],
    )
    assert not any(loaded.values()), f"forbidden modules imported by agent.webapp: {loaded}"


def test_agent_graph_does_not_import_exa_or_dashboard_routes() -> None:
    loaded = _closure_check(
        "agent.graphs.agent", ["exa_py", "agent.dashboard.routes", "agent.webapp"]
    )
    assert not any(loaded.values()), f"forbidden modules imported by agent.graphs.agent: {loaded}"


def test_runtime_does_not_import_the_graph_factories() -> None:
    """The shared runtime layer must not depend on the graphs that build on it."""
    loaded = _closure_check(
        "agent.runtime",
        [
            "agent.graphs.agent",
            "agent.graphs.reviewer",
            "agent.graphs.analyzer",
            "agent.graphs.chat",
            "agent.webapp",
        ],
    )
    assert not any(loaded.values()), f"forbidden modules imported by agent.runtime: {loaded}"


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
