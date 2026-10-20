"""Harbor host adapter for Managed Deep Agents eval artifacts."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from harbor.agents.base import BaseAgent

from mda_harbor import _trial_artifact, _trial_identity_path

if TYPE_CHECKING:
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext

_ADAPTER_PROTOCOL_VERSION = 1
_APP_DIR = "/app"
_AGENT_LOGS_DIR = "/logs/agent"
_ARTIFACT_DIR = "/mda-artifact"
_RUNNER_DIR = "/mda-runner"
_RUNTIME_DIR = "__runtime__"
# Eval identity fixture lives outside agent-writable /app (evals-final.md §8).
_IDENTITY_DIR = "/mda-eval"
_IDENTITY_PATH = f"{_IDENTITY_DIR}/identity.json"
_SETUP_SCRIPT = f"{_APP_DIR}/sandbox/setup.sh"


class ManagedDeepAgent(BaseAgent):
    """Run a compiled Managed Deep Agent artifact inside a Harbor trial."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        *args: Any,
        artifact_path: str,
        artifact_sha256: str | None = None,
        runtime: Literal["auto", "typescript", "python"] = "auto",
        adapter_protocol_version: int = _ADAPTER_PROTOCOL_VERSION,
        graph: str | None = None,
        identity_path: str | None = None,
        model_override: str | None = None,
        runtime_version: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.artifact_path = Path(artifact_path).expanduser().resolve()
        self.artifact_sha256 = artifact_sha256
        self.runtime = runtime
        self.adapter_protocol_version = adapter_protocol_version
        self.graph = graph
        self.identity_path = Path(identity_path).expanduser().resolve() if identity_path else None
        configured_model = self.model_name if _valid_model_name(self.model_name) else None
        self.model_override = model_override or configured_model
        self.runtime_version = runtime_version
        self._manifest: dict[str, Any] | None = None

    @staticmethod
    def name() -> str:
        return "mda-harbor"

    def version(self) -> str | None:
        if _valid_runtime_version(self.runtime_version):
            return self.runtime_version

        settings_path = self.artifact_path.parent / "runtime.json"
        try:
            settings: object = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        runtime_version = settings.get("runtime_version") if isinstance(settings, dict) else None
        if not _valid_runtime_version(runtime_version):
            return None
        self.runtime_version = runtime_version
        return runtime_version

    async def setup(self, environment: BaseEnvironment) -> None:
        self._apply_trial_artifact()
        manifest = self._load_manifest()

        # Always refresh runners (small; may change with the CLI build).
        await environment.upload_dir(Path(__file__).parent / "runners", _RUNNER_DIR)

        await environment.upload_dir(self._artifact_project_dir(), _ARTIFACT_DIR)
        result = await environment.exec(
            f"mkdir -p {_APP_DIR} && cp -a {_ARTIFACT_DIR}/. {_APP_DIR}/"
        )
        if result.return_code != 0:
            stderr = result.stderr or result.stdout or "artifact staging failed"
            raise RuntimeError(stderr)
        await self._install_runtime(environment, self._language(manifest))
        await self._upload_identity_fixture(environment)
        await self._run_setup_script(environment)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        manifest = self._manifest or self._load_manifest()
        language = self._language(manifest)
        runner = (
            f"{_RUNNER_DIR}/{'node_runner.mjs' if language == 'typescript' else 'python_runner.py'}"
        )
        summary_path = f"{_AGENT_LOGS_DIR}/summary.json"
        result_path = f"{_AGENT_LOGS_DIR}/result.json"
        instruction_path = f"{_AGENT_LOGS_DIR}/instruction.txt"

        await self._write_env_file(environment, instruction_path, instruction)
        command = self._runner_command(language, runner, instruction_path)
        result = await environment.exec(
            command, env=self._runner_env(language, summary_path, result_path)
        )
        if result.return_code != 0:
            stderr = result.stderr or result.stdout or "agent runner failed"
            raise RuntimeError(stderr)

        local_summary = self.logs_dir / "summary.json"
        await environment.download_file(summary_path, local_summary)
        summary = json.loads(local_summary.read_text(encoding="utf-8"))
        self._populate_context(context, summary)

    def _read_manifest(self) -> dict[str, Any]:
        manifest_path = self.artifact_path / "mda-eval-manifest.json"
        if not manifest_path.is_file():
            msg = f"missing eval manifest at {manifest_path}"
            raise FileNotFoundError(msg)
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _load_manifest(self) -> dict[str, Any]:
        manifest = self._read_manifest()
        self._validate_manifest(manifest)
        self._resolve_manifest_defaults(manifest)
        self._manifest = manifest
        return manifest

    def _validate_manifest(self, manifest: dict[str, Any]) -> None:
        protocol = manifest.get("adapter_protocol_version")
        if protocol != self.adapter_protocol_version:
            msg = (
                "unsupported MDA eval adapter protocol "
                f"{protocol!r}; expected {self.adapter_protocol_version}"
            )
            raise ValueError(msg)
        if self.artifact_sha256 and manifest.get("artifact_sha256") != self.artifact_sha256:
            msg = "eval artifact digest does not match adapter kwargs"
            raise ValueError(msg)
        self._language(manifest)
        graph = manifest.get("graph")
        if not isinstance(graph, str) or not graph.strip():
            msg = "eval artifact manifest graph must be a non-empty string"
            raise ValueError(msg)
        model_name = manifest.get("model_name")
        model_override = manifest.get("model_override")
        for field, value in (("model_name", model_name), ("model_override", model_override)):
            if value is not None and not _valid_model_name(value):
                msg = f"eval artifact manifest {field} must be null or a non-empty string"
                raise ValueError(msg)
        if model_override is not None and model_override != model_name:
            msg = "eval artifact manifest model_override must match model_name"
            raise ValueError(msg)
        runtime_version = manifest.get("runtime_version")
        if not _valid_runtime_version(runtime_version):
            msg = "eval artifact manifest runtime_version must be a non-empty string"
            raise ValueError(msg)
        if self.runtime_version is not None and runtime_version != self.runtime_version:
            msg = "eval artifact runtime version does not match the registered trial artifact"
            raise ValueError(msg)

    def _resolve_manifest_defaults(self, manifest: dict[str, Any]) -> None:
        if self.graph is None:
            self.graph = manifest["graph"]
        if not _valid_model_name(self.model_name) and manifest["model_name"] is not None:
            self.model_name = manifest["model_name"]
            self._init_model_info()
        if self.model_override is None:
            self.model_override = manifest["model_override"]
        if self.runtime_version is None:
            self.runtime_version = manifest["runtime_version"]

    def _apply_trial_artifact(self) -> None:
        binding = _trial_artifact(self.session_id, self.context_id)
        if binding is None:
            return
        self.artifact_path = binding.artifact_path
        self.artifact_sha256 = binding.artifact_sha256
        if self.graph is None:
            self.graph = binding.graph
        if not _valid_model_name(self.model_name) and binding.model_name is not None:
            self.model_name = binding.model_name
            self._init_model_info()
        self.model_override = binding.model_override
        self.runtime_version = binding.runtime_version

    def _language(self, manifest: dict[str, Any]) -> Literal["typescript", "python"]:
        language = manifest.get("language")
        if self.runtime != "auto":
            language = self.runtime
        if language not in {"typescript", "python"}:
            msg = f"unsupported MDA eval artifact language {language!r}"
            raise ValueError(msg)
        return language

    def _artifact_project_dir(self) -> Path:
        project = self.artifact_path / "project"
        if not project.is_dir():
            msg = f"missing eval project directory at {project}"
            raise FileNotFoundError(msg)
        return project

    async def _install_runtime(
        self,
        environment: BaseEnvironment,
        language: Literal["typescript", "python"],
    ) -> None:
        if language == "typescript":
            commands = [
                f"cd {_APP_DIR}",
                "npm install --silent",
                "mkdir -p node_modules",
                f"ln -sfn {_APP_DIR}/{_RUNTIME_DIR} node_modules/managed-deepagents",
                (
                    # TypeScript ESM imports use .js (and often extensionless)
                    # specifiers while sources remain .ts/.tsx. Symlink both so
                    # the tsx loader can resolve them from the app workspace.
                    "find . -type d -name node_modules -prune -o "
                    "-path './__runtime__' -prune -o "
                    "-type f \\( -name '*.ts' -o -name '*.tsx' "
                    "-o -name '*.mts' -o -name '*.cts' \\) "
                    "-exec sh -c '"
                    "for f do "
                    'case "$f" in '
                    '*.tsx) base="${f%.tsx}"; js="$base.js" ;; '
                    '*.mts) base="${f%.mts}"; js="$base.mjs" ;; '
                    '*.cts) base="${f%.cts}"; js="$base.cjs" ;; '
                    '*.ts) base="${f%.ts}"; js="$base.js" ;; '
                    "*) continue ;; "
                    "esac; "
                    'if [ ! -e "$base" ] && [ ! -L "$base" ]; then '
                    'ln -s "$(basename "$f")" "$base"; fi; '
                    'if [ ! -e "$js" ] && [ ! -L "$js" ]; then '
                    'ln -s "$(basename "$f")" "$js"; fi; '
                    "done' sh {} +"
                ),
            ]
        else:
            commands = [
                (
                    'python -c "from pathlib import Path; import re; '
                    "p=Path('/app/pyproject.toml'); "
                    "p.exists() and p.write_text(re.sub("
                    "r'managed-deepagents\\\\s*@\\\\s*file:[^\\\\s\\\"\\']+', "
                    "'managed-deepagents @ file:///app/__runtime__', p.read_text()))\""
                ),
                f"cd {_APP_DIR}",
                f"python -m pip install --quiet ./{_RUNTIME_DIR}",
                # Compiled artifacts always get a pyproject.toml; skip gracefully
                # if an older cached artifact is still missing one.
                "if [ -f pyproject.toml ]; then python -m pip install --quiet -e .; fi",
            ]
        result = await environment.exec(" && ".join(commands))
        if result.return_code != 0:
            stderr = result.stderr or result.stdout or "runtime install failed"
            raise RuntimeError(stderr)

    async def _upload_identity_fixture(self, environment: BaseEnvironment) -> None:
        """Stage task `identity.json` under `/mda-eval` (not under `/app`)."""
        identity_path = self.identity_path
        if identity_path is None:
            identity_path = _trial_identity_path(self.session_id, self.context_id)
        if identity_path is None or not identity_path.is_file():
            return
        mkdir = await environment.exec(f"mkdir -p {_IDENTITY_DIR}")
        if mkdir.return_code != 0:
            stderr = mkdir.stderr or mkdir.stdout or "failed to create identity dir"
            raise RuntimeError(stderr)
        await environment.upload_file(identity_path, _IDENTITY_PATH)

    async def _run_setup_script(self, environment: BaseEnvironment) -> None:
        """Run authored `sandbox/setup.sh` once after install (Harbor owns `/app`)."""
        # Match the managed runtime: invoke with bash so authored scripts can
        # use bashisms (`set -o pipefail`, etc.). `/bin/sh script` ignores the
        # shebang and fails on dash.
        result = await environment.exec(
            f"if [ -f {_SETUP_SCRIPT} ]; then "
            f"chmod +x {_SETUP_SCRIPT} && cd {_APP_DIR} && bash {_SETUP_SCRIPT}; "
            f"fi"
        )
        if result.return_code != 0:
            stderr = result.stderr or result.stdout or "sandbox/setup.sh failed"
            raise RuntimeError(stderr)

    async def _write_env_file(
        self,
        environment: BaseEnvironment,
        target_path: str,
        instruction: str,
    ) -> None:
        host_path = self.logs_dir / "instruction.txt"
        host_path.write_text(instruction, encoding="utf-8")
        await environment.upload_file(host_path, target_path)

    def _runner_command(
        self,
        language: Literal["typescript", "python"],
        runner: str,
        instruction_path: str,
    ) -> str:
        quoted_instruction = shlex.quote(instruction_path)
        if language == "typescript":
            return f"cd {_APP_DIR} && node --import tsx {shlex.quote(runner)} {quoted_instruction}"
        return f"python {shlex.quote(runner)} {quoted_instruction}"

    def _runner_env(self, language: str, summary_path: str, result_path: str) -> dict[str, str]:
        env = {
            "MDA_EVAL_PROJECT_DIR": _APP_DIR,
            "MDA_EVAL_APP_DIR": _APP_DIR,
            "MDA_EVAL_AGENT_LOGS_DIR": _AGENT_LOGS_DIR,
            "MDA_EVAL_SUMMARY_PATH": summary_path,
            "MDA_EVAL_RESULT_PATH": result_path,
            "MDA_EVAL_MODEL_NAME": self.model_name or "",
            "MDA_EVAL_MODEL_OVERRIDE": self.model_override or "",
            "MDA_EVAL_GRAPH": self.graph,
            "MDA_EVAL_IDENTITY_PATH": _IDENTITY_PATH,
            "HARBOR_SESSION_ID": self.session_id or "",
        }
        if language == "python":
            env["PYTHONPATH"] = f"{_ARTIFACT_DIR}:{_APP_DIR}"
        else:
            env["NODE_PATH"] = f"{_ARTIFACT_DIR}/node_modules"
        try:
            from harbor_langsmith import parent_env  # noqa: PLC0415
        except ModuleNotFoundError as error:
            if error.name != "harbor_langsmith":
                raise
        else:
            env.update(parent_env(self.context_id))
        return {key: value for key, value in env.items() if value}

    def _populate_context(self, context: AgentContext, summary: dict[str, Any]) -> None:
        usage = summary.get("usage")
        if isinstance(usage, dict):
            context.n_input_tokens = _int_or_none(
                usage.get("input_tokens") or usage.get("inputTokens")
            )
            context.n_output_tokens = _int_or_none(
                usage.get("output_tokens") or usage.get("outputTokens")
            )
            context.n_cache_tokens = _int_or_none(
                usage.get("cache_read_tokens") or usage.get("cacheReadTokens")
            )
        context.metadata = {
            **(context.metadata or {}),
            "mda_summary": summary,
            "answer_written": summary.get("answer_written") or summary.get("answerWritten"),
        }


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _valid_runtime_version(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not any(character.isspace() for character in value)
    )


def _valid_model_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
