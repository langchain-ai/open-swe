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


def _layer_files(layer: str) -> list[Path]:
    """Every module in ``layer``, whether it names a package or a single module."""
    package = _AGENT_ROOT / layer
    if package.is_dir():
        return sorted(package.rglob("*.py"))
    return [_AGENT_ROOT / f"{layer}.py"]


def _modules_importing(layer: str, *forbidden: str) -> dict[str, set[str]]:
    offenders: dict[str, set[str]] = {}
    for path in _layer_files(layer):
        hits = {
            name
            for name in _imported_packages(path)
            for prefix in forbidden
            if name == prefix or name.startswith(f"{prefix}.")
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


# What a run is built on: the integrations it talks to (GitHub, Slack, Linear,
# LangSmith), the sandboxes and models it runs on, the threads it runs in,
# stored settings, shared helpers, the store client, the environment.
# Everything else is built on top of these.
_FOUNDATION_LAYERS = (
    "config",
    "github",
    "langsmith",
    "linear",
    "models",
    "runtime",
    "sandboxes",
    "settings",
    "slack",
    "store",
    "threads",
    "utils",
)
# The layers built on that foundation: the web surface, the inbound adapters,
# the graphs, and the tools the graphs call.
_LAYERS_BUILT_ON_THE_FOUNDATION = (
    "agent.api",
    "agent.dashboard",
    "agent.graphs",
    "agent.tools",
    "agent.webhooks",
)


def test_the_foundation_does_not_import_what_is_built_on_it() -> None:
    """Stored settings and shared helpers must not reach back up into the app.

    ``agent.settings`` is where team/user/repo configuration lives, and every
    layer above reads it. If it — or ``agent.utils``/``agent.runtime``/the store
    and config modules, or any of the per-integration packages — imports the
    dashboard, a graph, a tool or a webhook, the dependency runs backwards:
    importing a setting drags FastAPI and the agent stack in with it, and the
    layers can no longer be reasoned about (or loaded) separately. A lazy import
    inside a function is the same edge.
    """
    offenders = {
        layer: hits
        for layer in _FOUNDATION_LAYERS
        if (hits := _modules_importing(layer, *_LAYERS_BUILT_ON_THE_FOUNDATION))
    }
    assert offenders == {}


def test_the_foundation_does_not_import_the_review_domain() -> None:
    """The reviewer is a domain above the foundation, not a peer of it."""
    offenders = {
        layer: hits
        for layer in _FOUNDATION_LAYERS
        if (hits := _modules_importing(layer, "agent.review"))
    }
    assert offenders == {}


def test_the_review_domain_does_not_import_the_app_layers() -> None:
    """The reviewer is driven by the webhook/dashboard/graph layers, not the reverse."""
    offenders = _modules_importing(
        "review", "agent.dashboard", "agent.graphs", "agent.tools", "agent.webhooks"
    )
    assert offenders == {}


# The dashboard is the HTTP surface. Where a graph, a tool, a webhook or the
# scheduler still calls into it, it is calling a *use case* that happens to live
# in an HTTP module — listing threads, dispatching a run, approving a plan,
# checking repo access. Each of those is a module that should eventually move
# down out of ``agent.dashboard``; naming them here keeps the set from growing
# while that lift is pending, and keeps ``agent.dashboard.__init__`` honest
# about why it still loads its router lazily.
_ALLOWED_DASHBOARD_EDGES = {
    "scheduling/agent_schedules.py": {"agent.dashboard.repo_access"},
    "tools/slack_start_new_thread.py": {"agent.dashboard.repo_access"},
    "tools/threads.py": {
        "agent.dashboard",  # plan_api + workflow_approval_api, imported as modules
        "agent.dashboard.oauth",
        "agent.dashboard.threads.listing",
        "agent.dashboard.threads.runs",
    },
    "webhooks/slack.py": {"agent.dashboard.plan_api"},
}


def test_the_graphs_and_their_callers_do_not_grow_new_dashboard_edges() -> None:
    """Only the listed modules may reach into the HTTP layer, for the listed reason.

    A new entry here is a new dependency from the agent onto FastAPI-shaped
    code, and it is what forces ``agent.dashboard.__init__`` to keep the router
    behind a lazy ``__getattr__``: importing one of these submodules must not
    drag ``agent.dashboard.routes`` into an agent run's import closure.
    """
    edges = {
        module: hits
        for layer in ("graphs", "scheduling", "tools", "webhooks")
        for module, hits in _modules_importing(layer, "agent.dashboard").items()
    }
    assert edges == _ALLOWED_DASHBOARD_EDGES


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
