"""Tests for the Harbor Python trial runner (identity mapping, invocation)."""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

_RUNNER = (
    Path(__file__).resolve().parents[3]
    / "crates"
    / "mda"
    / "harbor"
    / "mda_harbor"
    / "runners"
    / "python_runner.py"
)
_NODE_RUNNER = _RUNNER.with_name("node_runner.mjs")
_AGENT = _RUNNER.parents[1] / "agent.py"
_ADAPTER_INIT = _AGENT.parent / "__init__.py"
_JOB_PLUGIN = _RUNNER.parents[1] / "job_plugin.py"
_LANGSMITH_PLUGIN = _RUNNER.parents[1] / "langsmith_plugin.py"
_DEFAULT_JOB_ID = UUID("11111111-1111-4111-8111-111111111111")


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mda_harbor_python_runner", _RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_adapter_package(monkeypatch: Any) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mda_harbor",
        _ADAPTER_INIT,
        submodule_search_locations=[str(_ADAPTER_INIT.parent)],
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _load_job_plugin(monkeypatch: Any) -> ModuleType:
    class BaseJobPlugin:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

    harbor = ModuleType("harbor")
    harbor_models = ModuleType("harbor.models")
    harbor_job = ModuleType("harbor.models.job")
    harbor_plugin = ModuleType("harbor.models.job.plugin")
    harbor_plugin.__dict__["BaseJobPlugin"] = BaseJobPlugin
    monkeypatch.setitem(sys.modules, "harbor", harbor)
    monkeypatch.setitem(sys.modules, "harbor.models", harbor_models)
    monkeypatch.setitem(sys.modules, "harbor.models.job", harbor_job)
    monkeypatch.setitem(sys.modules, "harbor.models.job.plugin", harbor_plugin)
    _load_adapter_package(monkeypatch)

    spec = importlib.util.spec_from_file_location("mda_harbor.job_plugin", _JOB_PLUGIN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _load_langsmith_plugin(monkeypatch: Any, request_headers: list[dict[str, str]]) -> ModuleType:
    class UpstreamLangSmithPlugin:
        def __init__(self, *, workspace_id: str | None = None) -> None:
            self.workspace_id = workspace_id
            self._session = SimpleNamespace(headers={})
            self.setup_workspace_ids: list[str | None] = []
            self.handled_models: list[str | None] = []

        def _setup(self, job: object) -> None:
            del job
            self.setup_workspace_ids.append(self.workspace_id)
            self._session.headers.update({"x-api-key": "secret"})
            if self.workspace_id:
                self._session.headers.update({"LANGSMITH-WORKSPACE-ID": self.workspace_id})
            request_headers.append(dict(self._session.headers))

        def _handle_event_sync(self, event: Any) -> None:
            self.handled_models.append(event.config.agent.model_name)

    harbor_langsmith = ModuleType("harbor_langsmith")
    harbor_langsmith_plugin = ModuleType("harbor_langsmith.plugin")
    harbor_langsmith_plugin.__dict__["LangSmithPlugin"] = UpstreamLangSmithPlugin
    monkeypatch.setitem(sys.modules, "harbor_langsmith", harbor_langsmith)
    monkeypatch.setitem(sys.modules, "harbor_langsmith.plugin", harbor_langsmith_plugin)
    _load_adapter_package(monkeypatch)

    spec = importlib.util.spec_from_file_location("mda_harbor_langsmith_plugin", _LANGSMITH_PLUGIN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _load_agent(monkeypatch: Any) -> ModuleType:
    class BaseAgent:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args
            self.model_name = kwargs.pop("model_name", None)
            self.session_id = kwargs.pop("session_id", None)
            self.context_id = kwargs.pop("context_id", None)
            self.logs_dir = Path(kwargs.pop("logs_dir", "."))
            self._model_info: dict[str, str] | None = None
            self._init_model_info()

        def _init_model_info(self) -> None:
            self._model_info = (
                {"name": self.model_name} if isinstance(self.model_name, str) else None
            )

        def to_agent_info(self) -> SimpleNamespace:
            return SimpleNamespace(model_info=self._model_info)

    harbor = ModuleType("harbor")
    harbor_agents = ModuleType("harbor.agents")
    harbor_base = ModuleType("harbor.agents.base")
    harbor_base.__dict__["BaseAgent"] = BaseAgent
    monkeypatch.setitem(sys.modules, "harbor", harbor)
    monkeypatch.setitem(sys.modules, "harbor.agents", harbor_agents)
    monkeypatch.setitem(sys.modules, "harbor.agents.base", harbor_base)
    _load_adapter_package(monkeypatch)

    spec = importlib.util.spec_from_file_location("mda_harbor.agent", _AGENT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class _Environment:
    def __init__(self) -> None:
        self.exec_calls: list[tuple[str, dict[str, str] | None]] = []
        self.uploads: list[tuple[Path, str]] = []

    async def upload_dir(self, source: Path, target: str) -> None:
        del source, target

    async def upload_file(self, source: Path, target: str) -> None:
        self.uploads.append((source, target))

    async def exec(self, command: str, *, env: dict[str, str] | None = None) -> SimpleNamespace:
        self.exec_calls.append((command, env))
        return SimpleNamespace(return_code=0, stderr="", stdout="")


def _artifact(tmp_path: Path, manifest: object, name: str = "artifact") -> Path:
    artifact = tmp_path / name
    (artifact / "project").mkdir(parents=True)
    (artifact / "mda-eval-manifest.json").write_text(json.dumps(manifest))
    return artifact


def _disable_setup_side_effects(agent: Any, monkeypatch: Any) -> None:
    async def noop(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(agent, "_install_runtime", noop)
    monkeypatch.setattr(agent, "_upload_identity_fixture", noop)
    monkeypatch.setattr(agent, "_run_setup_script", noop)


def test_adapter_registry_keys_identity_by_session_and_context(
    tmp_path: Path, monkeypatch: Any
) -> None:
    adapter = _load_adapter_package(monkeypatch)
    identity = tmp_path / "identity.json"
    identity.write_text('{"user":{"id":"eval-user"}}')

    adapter._register_trial_identity("trial__agent", "context-a", identity)

    assert adapter._trial_identity_path("trial__agent", "context-a") == identity
    assert adapter._trial_identity_path("trial__agent", "context-b") is None
    adapter._clear_trial_identity("trial__agent", "context-a")
    assert adapter._trial_identity_path("trial__agent", "context-a") is None


def test_agent_uploads_only_identity_registered_for_its_trial(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    adapter = sys.modules["mda_harbor"]
    identity = tmp_path / "identity.json"
    identity.write_text('{"user":{"id":"eval-user"}}')
    adapter._register_trial_identity("identity-task__agent", "context-a", identity)
    adapter._register_trial_identity("other-task__agent", "context-b", tmp_path / "other.json")
    agent = agent_module.ManagedDeepAgent(artifact_path=str(tmp_path / "artifact"))
    agent.session_id = "identity-task__agent"
    agent.context_id = "context-a"
    environment = _Environment()

    asyncio.run(agent._upload_identity_fixture(environment))

    assert environment.uploads == [(identity, "/mda-eval/identity.json")]


def test_agent_explicit_identity_path_precedes_registered_identity(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    adapter = sys.modules["mda_harbor"]
    configured = tmp_path / "configured.json"
    configured.write_text('{"user":{"id":"configured"}}')
    registered = tmp_path / "registered.json"
    registered.write_text('{"user":{"id":"registered"}}')
    adapter._register_trial_identity("task__agent", "context-a", registered)
    agent = agent_module.ManagedDeepAgent(
        artifact_path=str(tmp_path / "artifact"), identity_path=str(configured)
    )
    agent.session_id = "task__agent"
    agent.context_id = "context-a"
    environment = _Environment()

    asyncio.run(agent._upload_identity_fixture(environment))

    assert environment.uploads == [(configured.resolve(), "/mda-eval/identity.json")]


@pytest.mark.parametrize("configured_model", [None, ""])
def test_agent_setup_resolves_graph_and_model_name_from_manifest(
    tmp_path: Path, monkeypatch: Any, configured_model: str | None
) -> None:
    agent_module = _load_agent(monkeypatch)
    artifact = _artifact(
        tmp_path,
        {
            "adapter_protocol_version": 1,
            "language": "python",
            "graph": "research-agent",
            "model_name": "openai:gpt-5-mini",
            "model_override": None,
            "runtime_version": "0.5.2",
        },
    )
    agent = agent_module.ManagedDeepAgent(artifact_path=str(artifact), model_name=configured_model)
    _disable_setup_side_effects(agent, monkeypatch)

    asyncio.run(agent.setup(_Environment()))

    assert agent.graph == "research-agent"
    assert agent.model_name == "openai:gpt-5-mini"
    assert agent.to_agent_info().model_info == {"name": "openai:gpt-5-mini"}
    assert agent._runner_env("python", "/summary", "/result") == {
        "MDA_EVAL_PROJECT_DIR": "/app",
        "MDA_EVAL_APP_DIR": "/app",
        "MDA_EVAL_AGENT_LOGS_DIR": "/logs/agent",
        "MDA_EVAL_SUMMARY_PATH": "/summary",
        "MDA_EVAL_RESULT_PATH": "/result",
        "MDA_EVAL_MODEL_NAME": "openai:gpt-5-mini",
        "MDA_EVAL_GRAPH": "research-agent",
        "MDA_EVAL_IDENTITY_PATH": "/mda-eval/identity.json",
        "PYTHONPATH": "/mda-artifact:/app",
    }


def test_agent_preserves_dynamic_authored_model_without_override(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    artifact = _artifact(
        tmp_path,
        {
            "adapter_protocol_version": 1,
            "language": "python",
            "graph": "research-agent",
            "model_name": None,
            "model_override": None,
            "runtime_version": "0.5.2",
        },
    )
    agent = agent_module.ManagedDeepAgent(artifact_path=str(artifact))
    _disable_setup_side_effects(agent, monkeypatch)

    asyncio.run(agent.setup(_Environment()))

    assert agent.model_name is None
    assert agent.model_override is None
    runner_env = agent._runner_env("python", "/summary", "/result")
    assert "MDA_EVAL_MODEL_NAME" not in runner_env
    assert "MDA_EVAL_MODEL_OVERRIDE" not in runner_env


def test_agent_propagates_explicit_model_override_separately_from_telemetry(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    artifact = _artifact(
        tmp_path,
        {
            "adapter_protocol_version": 1,
            "language": "typescript",
            "graph": "research-agent",
            "model_name": "anthropic:claude-sonnet-4-5",
            "model_override": "anthropic:claude-sonnet-4-5",
            "runtime_version": "0.5.2",
        },
    )
    agent = agent_module.ManagedDeepAgent(artifact_path=str(artifact))
    _disable_setup_side_effects(agent, monkeypatch)

    asyncio.run(agent.setup(_Environment()))

    assert agent.model_name == "anthropic:claude-sonnet-4-5"
    assert agent.model_override == "anthropic:claude-sonnet-4-5"
    runner_env = agent._runner_env("typescript", "/summary", "/result")
    assert runner_env["MDA_EVAL_MODEL_NAME"] == "anthropic:claude-sonnet-4-5"
    assert runner_env["MDA_EVAL_MODEL_OVERRIDE"] == "anthropic:claude-sonnet-4-5"


def test_agent_setup_preserves_explicit_graph_and_model_overrides(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    artifact = _artifact(
        tmp_path,
        {
            "adapter_protocol_version": 1,
            "language": "typescript",
            "graph": "manifest-graph",
            "model_name": "openai:gpt-5-mini",
            "model_override": None,
            "runtime_version": "0.5.2",
        },
    )
    agent = agent_module.ManagedDeepAgent(
        artifact_path=str(artifact),
        graph="configured-graph",
        model_name="anthropic:claude-sonnet-4-5",
    )
    model_info = agent.to_agent_info().model_info
    _disable_setup_side_effects(agent, monkeypatch)

    asyncio.run(agent.setup(_Environment()))

    assert agent.graph == "configured-graph"
    assert agent.model_name == "anthropic:claude-sonnet-4-5"
    assert agent.to_agent_info().model_info is model_info


def test_agent_setup_resolves_its_registered_job_artifact(tmp_path: Path, monkeypatch: Any) -> None:
    agent_module = _load_agent(monkeypatch)
    adapter = sys.modules["mda_harbor"]
    artifact = _artifact(
        tmp_path,
        {
            "schema_version": "mda.eval-artifact/v2",
            "artifact_sha256": "0123456789abcdef",
            "project_tree_hash": "fedcba9876543210",
            "language": "python",
            "node_version": None,
            "python_version": "3.11",
            "graph": "research-agent",
            "adapter_protocol_version": 1,
            "model_name": "openai:gpt-5-mini",
            "model_override": None,
            "langgraph_api_version": "0.12.0rc9",
            "source_revision": None,
            "dirty": False,
            "runtime_version": "0.5.2",
        },
        name="job-artifact",
    )
    fallback = tmp_path / ".mda" / "evals" / "artifact"
    agent = agent_module.ManagedDeepAgent(artifact_path=str(fallback))
    agent.session_id = "task__agent"
    agent.context_id = "11111111-1111-4111-8111-111111111111"
    adapter._register_trial_artifact(
        agent.session_id,
        agent.context_id,
        artifact_path=artifact,
        artifact_sha256="0123456789abcdef",
        graph="research-agent",
        model_name="openai:gpt-5-mini",
        runtime_version="0.5.2",
    )
    _disable_setup_side_effects(agent, monkeypatch)

    asyncio.run(agent.setup(_Environment()))

    assert agent.artifact_path == artifact.resolve()
    assert agent.graph == "research-agent"
    assert agent.model_name == "openai:gpt-5-mini"
    assert agent.version() == "0.5.2"
    assert agent.to_agent_info().model_info == {"name": "openai:gpt-5-mini"}


def test_agent_version_reads_runtime_settings_before_trial_start(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    artifact = tmp_path / ".mda" / "evals" / "artifact"
    artifact.parent.mkdir(parents=True)
    (artifact.parent / "runtime.json").write_text(json.dumps({"runtime_version": "0.5.2"}))
    agent = agent_module.ManagedDeepAgent(artifact_path=str(artifact))

    assert agent.version() == "0.5.2"


@pytest.mark.parametrize(
    "graph",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="blank"),
        pytest.param(7, id="wrong-type"),
    ],
)
def test_agent_setup_rejects_malformed_manifest_graph(
    tmp_path: Path, monkeypatch: Any, graph: object
) -> None:
    agent_module = _load_agent(monkeypatch)
    manifest: dict[str, object] = {
        "adapter_protocol_version": 1,
        "language": "python",
        "model_name": "openai:gpt-5-mini",
        "model_override": None,
    }
    if graph is not None:
        manifest["graph"] = graph
    agent = agent_module.ManagedDeepAgent(artifact_path=str(_artifact(tmp_path, manifest)))
    _disable_setup_side_effects(agent, monkeypatch)

    with pytest.raises(ValueError, match="graph"):
        asyncio.run(agent.setup(_Environment()))


@pytest.mark.parametrize("field", ["model_name", "model_override"])
@pytest.mark.parametrize("value", ["", "   ", 7])
def test_agent_setup_rejects_malformed_manifest_model_fields(
    tmp_path: Path, monkeypatch: Any, field: str, value: object
) -> None:
    agent_module = _load_agent(monkeypatch)
    manifest: dict[str, object] = {
        "adapter_protocol_version": 1,
        "language": "python",
        "graph": "research-agent",
        "model_name": None,
        "model_override": None,
    }
    manifest[field] = value
    agent = agent_module.ManagedDeepAgent(artifact_path=str(_artifact(tmp_path, manifest)))
    _disable_setup_side_effects(agent, monkeypatch)

    with pytest.raises(ValueError, match=field):
        asyncio.run(agent.setup(_Environment()))


def test_agent_run_without_setup_validates_manifest_before_runner_side_effects(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    artifact = _artifact(
        tmp_path,
        {
            "adapter_protocol_version": 1,
            "language": "python",
            "graph": "",
            "model_name": "openai:gpt-5-mini",
            "model_override": None,
        },
    )
    agent = agent_module.ManagedDeepAgent(artifact_path=str(artifact), logs_dir=tmp_path)

    with pytest.raises(ValueError, match="graph"):
        asyncio.run(agent.run("hello", _Environment(), SimpleNamespace()))


def test_agent_typescript_runner_uses_tsx_loader_from_app_directory(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    agent = agent_module.ManagedDeepAgent(artifact_path=str(tmp_path / "artifact"))

    command = agent._runner_command(
        "typescript", "/mda-runner/node_runner.mjs", "/logs/agent/instruction.txt"
    )

    assert command == (
        "cd /app && node --import tsx /mda-runner/node_runner.mjs /logs/agent/instruction.txt"
    )
    assert "--experimental-strip-types" not in command


def test_agent_typescript_runtime_linking_skips_dependencies_and_bundled_runtime(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    agent = agent_module.ManagedDeepAgent(artifact_path=str(tmp_path / "artifact"))
    environment = _Environment()

    asyncio.run(agent._install_runtime(environment, "typescript"))

    command = environment.exec_calls[0][0]
    assert "-type d -name node_modules -prune -o" in command
    assert "-path './__runtime__' -prune -o" in command


def test_agent_typescript_runtime_linking_preserves_authored_siblings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    agent = agent_module.ManagedDeepAgent(artifact_path=str(tmp_path / "artifact"))
    environment = _Environment()

    asyncio.run(agent._install_runtime(environment, "typescript"))

    command = environment.exec_calls[0][0]
    assert 'if [ ! -e "$base" ] && [ ! -L "$base" ]; then' in command
    assert 'if [ ! -e "$js" ] && [ ! -L "$js" ]; then' in command
    assert 'ln -sfn "$(basename "$f")"' not in command


def test_agent_runner_env_surfaces_broken_optional_langsmith_import(
    tmp_path: Path, monkeypatch: Any
) -> None:
    agent_module = _load_agent(monkeypatch)
    agent = agent_module.ManagedDeepAgent(
        artifact_path=str(tmp_path / "artifact"),
        graph="agent",
        model_name="openai:gpt-5-mini",
    )
    original_import = builtins.__import__

    def import_with_broken_dependency(
        name: str,
        global_vars: dict[str, Any] | None = None,
        local_vars: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "harbor_langsmith":
            raise ModuleNotFoundError(
                "No module named 'transitive_dependency'",
                name="transitive_dependency",
            )
        return original_import(name, global_vars, local_vars, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_with_broken_dependency)

    with pytest.raises(ModuleNotFoundError, match="transitive_dependency"):
        agent._runner_env("python", "/summary", "/result")


def _runtime_settings(
    tmp_path: Path, install_channel: str = "pypi"
) -> tuple[Path, Path, Path, Path]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    executable = tmp_path / "mda"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    bundled_runtime_path = tmp_path / "runtime"
    bundled_runtime_path.mkdir()
    marker = (
        bundled_runtime_path / "runtime/index.js"
        if install_channel == "npm"
        else bundled_runtime_path / "runtime.py"
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")
    settings = tmp_path / "runtime.json"
    settings.write_text(
        json.dumps(
            {
                "project_root": str(project_root),
                "mda_executable": str(executable),
                "install_channel": install_channel,
                "bundled_runtime_path": str(bundled_runtime_path),
                "runtime_version": "0.5.2",
            }
        )
    )
    return settings, project_root, executable, bundled_runtime_path


def test_normalized_task_path_collapses_redundant_parent_segments(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    project_root = tmp_path / "project"

    normalized = plugin_module._normalized_task_path(
        project_root,
        Path("evals") / ".." / "evals" / "task",
    )

    assert normalized == project_root / "evals" / "task"


class _Job:
    def __init__(
        self,
        agents: list[SimpleNamespace],
        job_id: UUID | None = None,
    ) -> None:
        self.id = job_id or _DEFAULT_JOB_ID
        self.config = SimpleNamespace(agents=agents)
        self._trial_configs = [SimpleNamespace(agent=agent) for agent in agents]
        self.trial_started_hooks: list[Any] = []
        self.trial_ended_hooks: list[Any] = []
        self.trial_cancelled_hooks: list[Any] = []

    def on_trial_started(self, callback: Any) -> _Job:
        self.trial_started_hooks.append(callback)
        return self

    def on_trial_ended(self, callback: Any) -> _Job:
        self.trial_ended_hooks.append(callback)
        return self

    def on_trial_cancelled(self, callback: Any) -> _Job:
        self.trial_cancelled_hooks.append(callback)
        return self


def _job(
    *agents: SimpleNamespace,
    job_id: UUID | None = None,
) -> _Job:
    return _Job(list(agents), job_id=job_id)


def _agent(
    import_path: str | None = "mda_harbor.agent:ManagedDeepAgent",
    model_name: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(import_path=import_path, model_name=model_name, kwargs={})


def _trial_event(
    task_path: Path,
    *,
    trial_name: str = "identity-task__abc1234",
    trial_id: str = "11111111-1111-4111-8111-111111111111",
    import_path: str = "mda_harbor.agent:ManagedDeepAgent",
) -> SimpleNamespace:
    return SimpleNamespace(
        trial_name=trial_name,
        trial_id=trial_id,
        config=SimpleNamespace(
            trial_name=trial_name,
            task=SimpleNamespace(path=task_path),
            agent=SimpleNamespace(import_path=import_path, model_name=None, kwargs={}),
        ),
    )


class _Process:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _compiled_artifact(argv: tuple[object, ...]) -> Path:
    artifact_flag = argv.index("--artifact-id")
    artifact_id = str(argv[artifact_flag + 1])
    project_root = Path(str(argv[3]))
    artifact = project_root / ".mda" / "evals" / "artifacts" / artifact_id
    if "--model" in argv:
        model_flag = argv.index("--model")
        model_override: str | None = str(argv[model_flag + 1])
        model_name: str | None = model_override
    else:
        model_override = None
        model_name = "openai:gpt-5-mini"
    (artifact / "project").mkdir(parents=True, exist_ok=True)
    (artifact / "mda-eval-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "mda.eval-artifact/v2",
                "artifact_sha256": "0123456789abcdef",
                "project_tree_hash": "fedcba9876543210",
                "language": "python",
                "node_version": None,
                "python_version": "3.11",
                "graph": "research-agent",
                "adapter_protocol_version": 1,
                "model_name": model_name,
                "model_override": model_override,
                "langgraph_api_version": "0.12.0rc9",
                "source_revision": None,
                "dirty": False,
                "runtime_version": "0.5.2",
            }
        )
    )
    return artifact


def _successful_compile(argv: tuple[object, ...]) -> _Process:
    _compiled_artifact(argv)
    return _Process()


def _mock_successful_compile(monkeypatch: Any) -> None:
    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        del kwargs
        return _successful_compile(argv)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)


def test_job_plugin_wires_and_clears_task_identity_with_public_hooks(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, project_root, _, _ = _runtime_settings(tmp_path)
    task = project_root / "evals" / "identity-task"
    task.mkdir(parents=True)
    identity = task / "identity.json"
    identity.write_text('{"user":{"id":"eval-user","kind":"person"}}')
    _mock_successful_compile(monkeypatch)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    job = _job(_agent())
    event = _trial_event(task)

    asyncio.run(plugin.on_job_start(job))
    assert len(job.trial_started_hooks) == 1
    assert len(job.trial_ended_hooks) == 1
    assert len(job.trial_cancelled_hooks) == 1
    asyncio.run(job.trial_started_hooks[0](event))

    adapter = sys.modules["mda_harbor"]
    session_id = f"{event.trial_name}__agent"
    assert adapter._trial_identity_path(session_id, event.trial_id) == identity.resolve()

    asyncio.run(job.trial_ended_hooks[0](event))
    assert adapter._trial_identity_path(session_id, event.trial_id) is None
    assert adapter._trial_artifact(session_id, event.trial_id) is None


def test_job_plugin_does_not_wire_identity_outside_project_dataset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)
    task = tmp_path / "outside-task"
    task.mkdir()
    (task / "identity.json").write_text('{"user":{"id":"outside-user"}}')
    _mock_successful_compile(monkeypatch)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    job = _job(_agent())
    event = _trial_event(task)

    asyncio.run(plugin.on_job_start(job))
    asyncio.run(job.trial_started_hooks[0](event))

    adapter = sys.modules["mda_harbor"]
    assert adapter._trial_identity_path(f"{event.trial_name}__agent", event.trial_id) is None


def test_job_plugin_rejects_identity_without_user_id(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, project_root, _, _ = _runtime_settings(tmp_path)
    task = project_root / "evals" / "identity-task"
    task.mkdir(parents=True)
    (task / "identity.json").write_text("{}")
    _mock_successful_compile(monkeypatch)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    job = _job(_agent())

    asyncio.run(plugin.on_job_start(job))

    with pytest.raises(RuntimeError, match=r"user\.id"):
        asyncio.run(job.trial_started_hooks[0](_trial_event(task)))


@pytest.mark.skipif(os.name == "nt", reason="symlink creation differs on Windows")
def test_job_plugin_rejects_symlinked_identity_fixture(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, project_root, _, _ = _runtime_settings(tmp_path)
    task = project_root / "evals" / "identity-task"
    task.mkdir(parents=True)
    outside = tmp_path / "outside-identity.json"
    outside.write_text('{"user":{"id":"outside-user"}}')
    (task / "identity.json").symlink_to(outside)
    _mock_successful_compile(monkeypatch)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    job = _job(_agent())

    asyncio.run(plugin.on_job_start(job))

    with pytest.raises(RuntimeError, match="symlink"):
        asyncio.run(job.trial_started_hooks[0](_trial_event(task)))


def test_job_plugin_clears_remaining_identity_on_job_end(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, project_root, _, _ = _runtime_settings(tmp_path)
    task = project_root / "evals" / "identity-task"
    task.mkdir(parents=True)
    (task / "identity.json").write_text('{"user":{"id":"eval-user"}}')
    _mock_successful_compile(monkeypatch)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    job = _job(_agent())
    event = _trial_event(task)

    asyncio.run(plugin.on_job_start(job))
    asyncio.run(job.trial_started_hooks[0](event))
    adapter = sys.modules["mda_harbor"]
    session_id = f"{event.trial_name}__agent"
    assert adapter._trial_identity_path(session_id, event.trial_id) is not None
    assert adapter._job_artifact(job.id) is not None
    artifact_path = adapter._job_artifact(job.id).artifact_path
    assert artifact_path.is_dir()

    asyncio.run(plugin.on_job_end(SimpleNamespace()))

    assert adapter._trial_identity_path(session_id, event.trial_id) is None
    assert adapter._job_artifact(job.id) is None
    assert not artifact_path.exists()


def test_job_plugin_compiles_matching_agent_with_model(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, project_root, executable, _ = _runtime_settings(tmp_path)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        calls.append((argv, kwargs))
        return _successful_compile(argv)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    job = _job(
        _agent("other.agent:Agent", "ignored:model"),
        _agent(model_name="anthropic:claude-sonnet-4-5"),
    )

    assert asyncio.run(plugin.on_job_start(job)) is None
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == (
        str(executable),
        "evals",
        "compile",
        str(project_root),
        "--artifact-id",
        "11111111-1111-4111-8111-111111111111",
        "--model",
        "anthropic:claude-sonnet-4-5",
    )
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE
    assert isinstance(kwargs.get("env"), dict)


def test_job_plugin_compiles_without_model_override(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, project_root, executable, _ = _runtime_settings(tmp_path)
    calls: list[tuple[object, ...]] = []

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        assert kwargs["stdout"] is asyncio.subprocess.PIPE
        assert kwargs["stderr"] is asyncio.subprocess.PIPE
        assert isinstance(kwargs.get("env"), dict)
        calls.append(argv)
        return _successful_compile(argv)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    asyncio.run(plugin.on_job_start(_job(_agent())))

    assert calls == [
        (
            str(executable),
            "evals",
            "compile",
            str(project_root),
            "--artifact-id",
            "11111111-1111-4111-8111-111111111111",
        )
    ]


def test_job_plugin_registers_artifact_without_mutating_harbor_configs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, project_root, _, _ = _runtime_settings(tmp_path)
    _mock_successful_compile(monkeypatch)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    job = _job(_agent())
    task = project_root / "evals" / "task"
    task.mkdir(parents=True)
    event = _trial_event(task)

    asyncio.run(plugin.on_job_start(job))
    asyncio.run(job.trial_started_hooks[0](event))

    expected_artifact = (
        project_root / ".mda" / "evals" / "artifacts" / "11111111-1111-4111-8111-111111111111"
    )
    assert job.config.agents[0] is job._trial_configs[0].agent
    assert job.config.agents[0].model_name is None
    assert job.config.agents[0].kwargs == {}
    assert event.config.agent.model_name is None
    binding = sys.modules["mda_harbor"]._trial_artifact(
        f"{event.trial_name}__agent", event.trial_id
    )
    assert binding.artifact_path == expected_artifact.resolve()
    assert binding.artifact_sha256 == "0123456789abcdef"
    assert binding.graph == "research-agent"
    assert binding.model_name == "openai:gpt-5-mini"
    assert binding.runtime_version == "0.5.2"


def test_job_plugin_uses_distinct_artifacts_for_distinct_job_ids(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)
    _mock_successful_compile(monkeypatch)
    first = _job(_agent(), job_id=UUID("11111111-1111-4111-8111-111111111111"))
    second = _job(_agent(), job_id=UUID("22222222-2222-4222-8222-222222222222"))

    project_root = Path(json.loads(settings.read_text())["project_root"])
    first_task = project_root / "evals" / "first"
    second_task = project_root / "evals" / "second"
    first_task.mkdir(parents=True)
    second_task.mkdir()
    first_event = _trial_event(
        first_task,
        trial_name="first__abc1234",
        trial_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    second_event = _trial_event(
        second_task,
        trial_name="second__abc1234",
        trial_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    first_plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    second_plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)
    asyncio.run(first_plugin.on_job_start(first))
    asyncio.run(second_plugin.on_job_start(second))
    asyncio.run(first.trial_started_hooks[0](first_event))
    asyncio.run(second.trial_started_hooks[0](second_event))

    adapter = sys.modules["mda_harbor"]
    first_binding = adapter._trial_artifact(
        f"{first_event.trial_name}__agent", first_event.trial_id
    )
    second_binding = adapter._trial_artifact(
        f"{second_event.trial_name}__agent", second_event.trial_id
    )
    assert first_binding.artifact_path != second_binding.artifact_path


def test_job_plugin_restores_persisted_launcher_env_when_host_vars_are_unset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, bundled_runtime_path = _runtime_settings(tmp_path)
    monkeypatch.delenv("MDA_INSTALL_CHANNEL", raising=False)
    monkeypatch.delenv("MDA_BUNDLED_RUNTIME_PATH", raising=False)
    monkeypatch.delenv("MDA_RUNTIME_VERSION", raising=False)
    monkeypatch.setenv("MDA_TEST_INHERITED", "keep-me")
    observed: tuple[object, object, object, object] | None = None

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        nonlocal observed
        env = kwargs.get("env")
        if isinstance(env, dict):
            observed = (
                env.get("MDA_INSTALL_CHANNEL"),
                env.get("MDA_BUNDLED_RUNTIME_PATH"),
                env.get("MDA_RUNTIME_VERSION"),
                env.get("MDA_TEST_INHERITED"),
            )
        return _successful_compile(argv)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    asyncio.run(plugin.on_job_start(_job(_agent())))

    assert observed == ("pypi", str(bundled_runtime_path), "0.5.2", "keep-me")


def test_job_plugin_overrides_stale_runtime_version_without_mutating_parent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)
    monkeypatch.setenv("MDA_RUNTIME_VERSION", "9.9.9")
    observed: object = None

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        nonlocal observed
        env = kwargs.get("env")
        if isinstance(env, dict):
            observed = env.get("MDA_RUNTIME_VERSION")
        return _successful_compile(argv)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    asyncio.run(plugin.on_job_start(_job(_agent())))

    assert observed == "0.5.2"
    assert os.environ["MDA_RUNTIME_VERSION"] == "9.9.9"


def test_job_plugin_nonzero_exit_prefers_concise_stderr(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        del argv, kwargs
        return _Process(
            returncode=2,
            stdout=b"stdout fallback",
            stderr=("specific failure " + "x" * 4_000).encode(),
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(plugin.on_job_start(_job(_agent())))

    message = str(exc_info.value)
    assert message.startswith("MDA eval artifact compile failed: specific failure")
    assert "stdout fallback" not in message
    assert len(message) < 1_200


def test_job_plugin_nonzero_exit_falls_back_to_stdout(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        del argv, kwargs
        return _Process(returncode=1, stdout=b"compile stdout", stderr=b" \n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(
        RuntimeError,
        match=r"^MDA eval artifact compile failed: compile stdout$",
    ):
        asyncio.run(plugin.on_job_start(_job(_agent())))


@pytest.mark.parametrize("unusable", ["missing", "not-executable"])
def test_job_plugin_rejects_missing_or_unusable_executable_before_launch(
    tmp_path: Path, monkeypatch: Any, unusable: str
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, executable, _ = _runtime_settings(tmp_path)
    if unusable == "missing":
        executable.unlink()
    else:
        executable.chmod(0o644)
    launched = False

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        nonlocal launched
        del argv, kwargs
        launched = True
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(RuntimeError, match="rerun `mda evals init`"):
        asyncio.run(plugin.on_job_start(_job(_agent())))

    assert launched is False


def test_job_plugin_reports_subprocess_launch_error(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)

    async def create_subprocess_exec(*argv: object, **kwargs: object) -> _Process:
        del argv, kwargs
        raise OSError("permission denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(
        RuntimeError,
        match="Could not launch MDA eval artifact compile: permission denied",
    ):
        asyncio.run(plugin.on_job_start(_job(_agent())))


def test_job_plugin_does_not_mislabel_post_launch_os_errors(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)
    _mock_successful_compile(monkeypatch)

    def fail_to_load_artifact(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("manifest read denied")

    monkeypatch.setattr(plugin_module, "_load_compiled_artifact", fail_to_load_artifact)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(OSError, match="manifest read denied"):
        asyncio.run(plugin.on_job_start(_job(_agent())))


def test_job_plugin_requires_exactly_one_matching_agent(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    for job in (
        _job(_agent("other.agent:Agent")),
        _job(_agent(), _agent()),
    ):
        with pytest.raises(RuntimeError, match="exactly one"):
            asyncio.run(plugin.on_job_start(job))


def test_job_plugin_rejects_invalid_runtime_settings_before_launch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    _, project_root, executable, bundled_runtime_path = _runtime_settings(tmp_path)

    async def unexpected_subprocess(*argv: object, **kwargs: object) -> _Process:
        del argv, kwargs
        raise AssertionError("invalid settings must not launch a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_subprocess)
    valid = {
        "project_root": str(project_root),
        "mda_executable": str(executable),
        "install_channel": "pypi",
        "bundled_runtime_path": str(bundled_runtime_path),
        "runtime_version": "0.5.2",
    }
    invalid_values: list[object] = [
        {},
        {**valid, "extra": True},
        {**valid, "project_root": "relative"},
        {**valid, "mda_executable": ""},
        {**valid, "project_root": 1},
        {**valid, "install_channel": 1},
        {**valid, "bundled_runtime_path": 1},
        {**valid, "runtime_version": 1},
    ]

    for index, value in enumerate(invalid_values):
        path = tmp_path / f"runtime-{index}.json"
        path.write_text(json.dumps(value))
        plugin = plugin_module.MDAJobPlugin(runtime_settings_path=path)
        with pytest.raises(RuntimeError, match="runtime settings"):
            asyncio.run(plugin.on_job_start(_job(_agent())))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("install_channel", "pip", "install_channel"),
        ("bundled_runtime_path", "relative/runtime", "paths must be absolute"),
        ("runtime_version", "", "runtime_version"),
        ("runtime_version", "0.5.2 stale", "runtime_version"),
    ],
)
def test_job_plugin_rejects_invalid_launcher_settings_before_launch(
    tmp_path: Path,
    monkeypatch: Any,
    field: str,
    value: str,
    message: str,
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, _ = _runtime_settings(tmp_path)
    contents = json.loads(settings.read_text())
    contents[field] = value
    settings.write_text(json.dumps(contents))

    async def unexpected_subprocess(*argv: object, **kwargs: object) -> _Process:
        del argv, kwargs
        raise AssertionError("invalid launcher settings must not launch a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_subprocess)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(RuntimeError, match=message):
        asyncio.run(plugin.on_job_start(_job(_agent())))


@pytest.mark.parametrize(
    ("install_channel", "marker"),
    [("npm", "runtime/index.js"), ("pypi", "runtime.py")],
)
def test_job_plugin_rejects_missing_runtime_marker_before_launch(
    tmp_path: Path,
    monkeypatch: Any,
    install_channel: str,
    marker: str,
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings, _, _, bundled_runtime_path = _runtime_settings(tmp_path, install_channel)
    (bundled_runtime_path / marker).unlink()

    async def unexpected_subprocess(*argv: object, **kwargs: object) -> _Process:
        del argv, kwargs
        raise AssertionError("missing runtime marker must not launch a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_subprocess)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(RuntimeError, match=r"bundled runtime.*missing or unusable"):
        asyncio.run(plugin.on_job_start(_job(_agent())))


def test_job_plugin_rejects_invalid_runtime_settings_json(tmp_path: Path, monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    settings = tmp_path / "runtime.json"
    settings.write_text("{not-json")
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=settings)

    with pytest.raises(RuntimeError, match="invalid JSON"):
        asyncio.run(plugin.on_job_start(_job(_agent())))


def test_job_plugin_missing_runtime_settings_fails_clearly(
    tmp_path: Path, monkeypatch: Any
) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    plugin = plugin_module.MDAJobPlugin(runtime_settings_path=tmp_path / "missing.json")

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(plugin.on_job_start(_job(_agent())))

    message = str(exc_info.value).lower()
    assert "runtime settings" in message
    assert "rerun `mda evals init`" in message


def test_job_plugin_on_job_end_is_noop(monkeypatch: Any) -> None:
    plugin_module = _load_job_plugin(monkeypatch)
    plugin = plugin_module.MDAJobPlugin()

    assert asyncio.run(plugin.on_job_end(SimpleNamespace())) is None


def test_langsmith_plugin_uses_tenant_header_during_setup_and_restores_workspace(
    monkeypatch: Any,
) -> None:
    request_headers: list[dict[str, str]] = []
    plugin_module = _load_langsmith_plugin(monkeypatch, request_headers)
    plugin = plugin_module.LangSmithPlugin(workspace_id="workspace-123")
    plugin._session.headers["LANGSMITH-WORKSPACE-ID"] = "stale-workspace"

    plugin._setup(object())

    assert request_headers == [{"X-Tenant-Id": "workspace-123", "x-api-key": "secret"}]
    assert plugin._session.headers == request_headers[0]
    assert plugin.workspace_id == "workspace-123"
    assert plugin.setup_workspace_ids == [None]


def test_langsmith_plugin_without_workspace_delegates_without_header_changes(
    monkeypatch: Any,
) -> None:
    request_headers: list[dict[str, str]] = []
    plugin_module = _load_langsmith_plugin(monkeypatch, request_headers)
    plugin = plugin_module.LangSmithPlugin()
    plugin._session.headers["LANGSMITH-WORKSPACE-ID"] = "upstream-sentinel"

    plugin._setup(object())

    assert request_headers == [
        {"LANGSMITH-WORKSPACE-ID": "upstream-sentinel", "x-api-key": "secret"}
    ]
    assert "X-Tenant-Id" not in plugin._session.headers
    assert plugin.setup_workspace_ids == [None]


def test_langsmith_plugin_uses_job_artifact_model_without_mutating_event(
    tmp_path: Path, monkeypatch: Any
) -> None:
    request_headers: list[dict[str, str]] = []
    plugin_module = _load_langsmith_plugin(monkeypatch, request_headers)
    adapter = sys.modules["mda_harbor"]
    adapter._register_job_artifact(
        _DEFAULT_JOB_ID,
        artifact_path=tmp_path / "artifact",
        artifact_sha256="0123456789abcdef",
        graph="research-agent",
        model_name="openai:gpt-5-mini",
        runtime_version="0.5.2",
    )
    original_agent = SimpleNamespace(
        import_path="mda_harbor.agent:ManagedDeepAgent", model_name=None
    )
    event = SimpleNamespace(config=SimpleNamespace(job_id=_DEFAULT_JOB_ID, agent=original_agent))
    plugin = plugin_module.LangSmithPlugin()

    plugin._handle_event_sync(event)

    assert plugin.handled_models == ["openai:gpt-5-mini"]
    assert event.config.agent is original_agent
    assert event.config.agent.model_name is None


def test_apply_identity_fixture_maps_auth_user() -> None:
    runner = _load_runner()
    configurable: dict = {"cwd": "/app", "thread_id": "t1"}
    runner._apply_identity_fixture(
        configurable,
        {
            "user": {"kind": "person", "id": "eval_user_1", "email": "eval@example.com"},
            # Legacy organization field is ignored (organization identity removed).
            "organization": {"id": "acme"},
            "source": {"provider": "cli"},
            "claims": {"permissions": ["billing:read"]},
        },
    )
    user = configurable["langgraph_auth_user"]
    assert user["mda_user_id"] == "eval_user_1"
    assert user["mda_user_kind"] == "person"
    assert user["mda_user_email"] == "eval@example.com"
    assert "mda_organization_id" not in user
    assert user["mda_claims"] == {"permissions": ["billing:read"]}
    assert configurable["mda_source_provider"] == "cli"


@pytest.mark.parametrize("user_id", ["", "   ", "\t\n"])
def test_apply_identity_fixture_ignores_blank_user_id(user_id: str) -> None:
    runner = _load_runner()
    configurable: dict[str, Any] = {"cwd": "/app", "thread_id": "t1"}

    runner._apply_identity_fixture(configurable, {"user": {"id": user_id}})

    assert "langgraph_auth_user" not in configurable


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    ("identity", "expect_user", "expect_claims"),
    [
        ({"user": {"id": "   "}}, False, False),
        ({"user": {"id": "eval-user"}, "claims": ["not", "an", "object"]}, True, False),
    ],
)
def test_node_runner_validates_identity_fixture_shape(
    tmp_path: Path,
    identity: dict[str, Any],
    expect_user: bool,
    expect_claims: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "./agent.mjs:createAgent"}}),
        encoding="utf-8",
    )
    (project / "agent.mjs").write_text(
        "export function createAgent(factoryConfig) {\n"
        "  return {\n"
        "    async invoke(_payload, runConfig) {\n"
        "      return { factoryConfig, runConfig };\n"
        "    },\n"
        "  };\n"
        "}\n",
        encoding="utf-8",
    )
    instruction_path = tmp_path / "instruction.txt"
    instruction_path.write_text("test identity", encoding="utf-8")
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    summary_path = tmp_path / "logs" / "summary.json"
    result_path = tmp_path / "logs" / "result.json"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "MDA_EVAL_PROJECT_DIR": str(project),
        "MDA_EVAL_APP_DIR": "/app",
        "MDA_EVAL_MODEL_NAME": "openai:gpt-5-mini",
        "MDA_EVAL_GRAPH": "agent",
        "MDA_EVAL_SUMMARY_PATH": str(summary_path),
        "MDA_EVAL_RESULT_PATH": str(result_path),
        "MDA_EVAL_IDENTITY_PATH": str(identity_path),
        "HARBOR_SESSION_ID": "identity-test",
    }

    completed = subprocess.run(
        ["node", str(_NODE_RUNNER), str(instruction_path)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    configurable = result["factoryConfig"]["configurable"]
    assert ("langgraph_auth_user" in configurable) is expect_user
    if expect_user:
        user = configurable["langgraph_auth_user"]
        assert ("mda_claims" in user) is expect_claims


class _AsyncGraph:
    """Compiled graph stub exposing both invocation modes, like LangGraph."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ainvoke(self, payload: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("ainvoke")
        return {"messages": [], "config": config, "payload": payload}

    def invoke(self, payload: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("invoke")
        return {"messages": []}


def test_invoke_graph_prefers_async_invocation() -> None:
    """Async-only connector tools reject sync invocation, so trials must await."""
    runner = _load_runner()
    graph = _AsyncGraph()
    result = runner._invoke_graph(
        graph,
        "do the thing",
        app_dir="/app",
        session_id="s1",
        identity=None,
    )
    assert graph.calls == ["ainvoke"]
    assert result["config"]["configurable"]["thread_id"] == "s1"


def test_load_graph_awaits_async_factory(tmp_path: Path) -> None:
    runner = _load_runner()
    project = tmp_path / "project"
    project.mkdir()
    (project / "langgraph.json").write_text(
        json.dumps({"graphs": {"agent": "./async_entry.py:agent"}}),
        encoding="utf-8",
    )
    (project / "async_entry.py").write_text(
        "async def agent(config):\n    return {'factory_config': config}\n",
        encoding="utf-8",
    )

    graph = runner._load_graph(
        project,
        "agent",
        model=None,
        app_dir="/app",
        session_id="trial-1",
        identity=None,
    )

    assert graph["factory_config"]["configurable"]["thread_id"] == "trial-1"


def test_invoke_graph_falls_back_to_sync_invoke() -> None:
    runner = _load_runner()

    class _SyncGraph:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def invoke(self, payload: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
            self.calls.append("invoke")
            return {"messages": []}

    graph = _SyncGraph()
    runner._invoke_graph(graph, "go", app_dir="/app", session_id="s1", identity=None)
    assert graph.calls == ["invoke"]


def test_langsmith_tracing_context_uses_harbor_parent(monkeypatch: Any) -> None:
    runner = _load_runner()
    captured: dict[str, Any] = {}

    @contextmanager
    def tracing_context(**kwargs: Any) -> Iterator[None]:
        captured.update(kwargs)
        yield

    run_helpers = ModuleType("langsmith.run_helpers")
    run_helpers.__dict__["tracing_context"] = tracing_context
    monkeypatch.setitem(sys.modules, "langsmith.run_helpers", run_helpers)
    monkeypatch.setenv("HARBOR_LANGSMITH_PARENT", "parent-trace")
    monkeypatch.setenv("HARBOR_LANGSMITH_BAGGAGE", "langsmith-project=mda-evals")

    with runner._langsmith_tracing_context():
        pass

    assert captured == {
        "parent": {
            "langsmith-trace": "parent-trace",
            "baggage": "langsmith-project=mda-evals",
        },
        "enabled": True,
    }
