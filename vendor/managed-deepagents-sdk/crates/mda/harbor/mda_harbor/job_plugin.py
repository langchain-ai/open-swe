"""Harbor job plugin that compiles the MDA eval artifact before trials start."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from harbor.models.job.plugin import BaseJobPlugin

from mda_harbor import (
    _ArtifactBinding,
    _clear_job_artifact,
    _clear_trial_artifact,
    _clear_trial_identity,
    _register_job_artifact,
    _register_trial_artifact,
    _register_trial_identity,
)

if TYPE_CHECKING:
    from harbor.job import Job
    from harbor.models.job.result import JobResult
    from harbor.trial.hooks import TrialHookEvent


_MDA_AGENT_IMPORT_PATH = "mda_harbor.agent:ManagedDeepAgent"
_INSTALL_CHANNEL_ENV = "MDA_INSTALL_CHANNEL"
_BUNDLED_RUNTIME_PATH_ENV = "MDA_BUNDLED_RUNTIME_PATH"
_RUNTIME_VERSION_ENV = "MDA_RUNTIME_VERSION"
_RUNTIME_SETTING_KEYS = frozenset(
    {
        "project_root",
        "mda_executable",
        "install_channel",
        "bundled_runtime_path",
        "runtime_version",
    }
)
_MAX_DIAGNOSTIC_LENGTH = 1_000
_ARTIFACT_DIGEST_LENGTH = 16


@dataclass(frozen=True)
class _RuntimeSettings:
    project_root: Path
    mda_executable: Path
    install_channel: str
    bundled_runtime_path: Path
    runtime_version: str


def _runtime_settings_path() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime.json"


def _load_runtime_settings(path: Path) -> _RuntimeSettings:
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RuntimeError(
            "MDA eval runtime settings could not be read; rerun `mda evals init` for this project."
        ) from error

    try:
        value: object = json.loads(contents)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "MDA eval runtime settings are invalid JSON; rerun `mda evals init` for this project."
        ) from error

    if not isinstance(value, dict) or set(value) != _RUNTIME_SETTING_KEYS:
        raise RuntimeError(
            "MDA eval runtime settings must contain exactly project_root, "
            "mda_executable, install_channel, bundled_runtime_path, and runtime_version; "
            "rerun `mda evals init` for this project."
        )

    project_root_value = value.get("project_root")
    mda_executable_value = value.get("mda_executable")
    install_channel_value = value.get("install_channel")
    bundled_runtime_path_value = value.get("bundled_runtime_path")
    runtime_version_value = value.get("runtime_version")
    if (
        not isinstance(project_root_value, str)
        or not project_root_value
        or not isinstance(mda_executable_value, str)
        or not mda_executable_value
        or not isinstance(bundled_runtime_path_value, str)
        or not bundled_runtime_path_value
    ):
        raise RuntimeError(
            "MDA eval runtime settings paths must be nonempty strings; "
            "rerun `mda evals init` for this project."
        )
    if not isinstance(install_channel_value, str) or install_channel_value not in {
        "npm",
        "pypi",
    }:
        raise RuntimeError(
            'MDA eval runtime settings install_channel must be "npm" or "pypi"; '
            "rerun `mda evals init` for this project."
        )
    if (
        not isinstance(runtime_version_value, str)
        or not runtime_version_value
        or any(character.isspace() for character in runtime_version_value)
    ):
        raise RuntimeError(
            "MDA eval runtime settings runtime_version must be a nonempty string without "
            "whitespace; rerun `mda evals init` for this project."
        )

    project_root = Path(project_root_value)
    mda_executable = Path(mda_executable_value)
    bundled_runtime_path = Path(bundled_runtime_path_value)
    if (
        not project_root.is_absolute()
        or not mda_executable.is_absolute()
        or not bundled_runtime_path.is_absolute()
    ):
        raise RuntimeError(
            "MDA eval runtime settings paths must be absolute; "
            "rerun `mda evals init` for this project."
        )

    return _RuntimeSettings(
        project_root=project_root,
        mda_executable=mda_executable,
        install_channel=install_channel_value,
        bundled_runtime_path=bundled_runtime_path,
        runtime_version=runtime_version_value,
    )


def _validate_runtime_paths(settings: _RuntimeSettings) -> None:
    if not settings.project_root.is_dir():
        raise RuntimeError(
            f"MDA eval project root is not an existing directory: {settings.project_root}"
        )
    if not settings.mda_executable.is_file() or not os.access(settings.mda_executable, os.X_OK):
        raise RuntimeError(
            "The MDA executable recorded for evals is missing or unusable; "
            "rerun `mda evals init` for this project."
        )
    runtime_marker = (
        settings.bundled_runtime_path / "runtime/index.js"
        if settings.install_channel == "npm"
        else settings.bundled_runtime_path / "runtime.py"
    )
    if not settings.bundled_runtime_path.is_dir() or not runtime_marker.is_file():
        raise RuntimeError(
            "The bundled runtime recorded for evals is missing or unusable; "
            "rerun `mda evals init` for this project."
        )


def _job_artifact_id(job: Job) -> str:
    try:
        return str(UUID(str(job.id)))
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError("Harbor job ID must be a UUID for MDA artifact isolation.") from error


def _compile_argv(job: Job, settings: _RuntimeSettings, artifact_id: str) -> list[str]:
    matching_agents = [
        agent for agent in job.config.agents if agent.import_path == _MDA_AGENT_IMPORT_PATH
    ]
    if len(matching_agents) != 1:
        raise RuntimeError(
            "MDA eval artifact compile requires exactly one agent with import_path "
            f"{_MDA_AGENT_IMPORT_PATH!r}; found {len(matching_agents)}."
        )

    argv = [
        str(settings.mda_executable),
        "evals",
        "compile",
        str(settings.project_root),
        "--artifact-id",
        artifact_id,
    ]
    model_name = matching_agents[0].model_name
    if model_name is not None and model_name.strip():
        argv.extend(("--model", model_name.strip()))
    return argv


def _artifact_path(settings: _RuntimeSettings, artifact_id: str) -> Path:
    return settings.project_root / ".mda" / "evals" / "artifacts" / artifact_id


def _optional_manifest_model(manifest: dict[object, object], field: str) -> str | None:
    value = manifest.get(field)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise RuntimeError(f"MDA eval artifact manifest has an invalid {field}.")
    return value


def _load_compiled_artifact(
    artifact_path: Path,
    expected_runtime_version: str,
) -> _ArtifactBinding:
    resolved_artifact = _require_real_directory(artifact_path, "artifact directory")
    if resolved_artifact != artifact_path:
        raise RuntimeError("MDA eval artifact directory resolved outside its expected path.")

    manifest_path = artifact_path / "mda-eval-manifest.json"
    try:
        metadata = manifest_path.lstat()
        contents = manifest_path.read_text(encoding="utf-8")
        manifest: object = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("MDA eval artifact manifest could not be read.") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("MDA eval artifact manifest must be a regular file.")
    if not isinstance(manifest, dict):
        raise RuntimeError("MDA eval artifact manifest must be a JSON object.")

    digest = manifest.get("artifact_sha256")
    graph = manifest.get("graph")
    model_name = _optional_manifest_model(manifest, "model_name")
    model_override = _optional_manifest_model(manifest, "model_override")
    runtime_version = manifest.get("runtime_version")
    if (
        not isinstance(digest, str)
        or len(digest) != _ARTIFACT_DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise RuntimeError("MDA eval artifact manifest has an invalid digest.")
    if not isinstance(graph, str) or not graph.strip():
        raise RuntimeError("MDA eval artifact manifest has an invalid graph.")
    if model_override is not None and model_override != model_name:
        raise RuntimeError("MDA eval artifact model_override must match model_name.")
    if runtime_version != expected_runtime_version:
        raise RuntimeError("MDA eval artifact manifest has an unexpected runtime version.")

    return _ArtifactBinding(
        artifact_path=resolved_artifact,
        artifact_sha256=digest,
        graph=graph,
        model_name=model_name,
        model_override=model_override,
        runtime_version=runtime_version,
    )


def _remove_generated_artifact(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("MDA generated eval artifact cleanup target must be a directory.")
    shutil.rmtree(path)


def _concise_diagnostic(message: str) -> str:
    if len(message) > _MAX_DIAGNOSTIC_LENGTH:
        return f"{message[: _MAX_DIAGNOSTIC_LENGTH - 3]}..."
    return message


def _process_diagnostic(stdout: bytes, stderr: bytes, returncode: int) -> str:
    message = stderr.decode("utf-8", errors="replace").strip()
    if not message:
        message = stdout.decode("utf-8", errors="replace").strip()
    if not message:
        message = f"process exited with status {returncode}"
    return _concise_diagnostic(message)


def _normalized_task_path(project_root: Path, task_path: Path) -> Path:
    if not task_path.is_absolute():
        task_path = project_root / task_path
    return Path(os.path.normpath(task_path.absolute()))


def _require_real_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError(f"MDA eval {label} is not an existing directory.") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"MDA eval {label} must not be a symlink.")
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"MDA eval {label} must be a directory.")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"MDA eval {label} could not be resolved.") from error


def _validated_task_path(project_root: Path, task_path_value: object) -> Path | None:
    if not isinstance(task_path_value, (str, os.PathLike)):
        return None

    dataset_path = _normalized_task_path(project_root, Path("evals"))
    task_path = _normalized_task_path(project_root, Path(task_path_value))
    if task_path.parent != dataset_path:
        return None

    resolved_dataset = _require_real_directory(dataset_path, "dataset directory")
    resolved_task = _require_real_directory(task_path, "task directory")
    if resolved_task.parent != resolved_dataset:
        raise RuntimeError("MDA eval task directory must be a direct child of the dataset.")
    return resolved_task


def _validated_identity_file(task_path: Path) -> Path | None:
    identity_path = task_path / "identity.json"

    try:
        identity_metadata = identity_path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeError("MDA eval task identity.json could not be inspected.") from error
    if stat.S_ISLNK(identity_metadata.st_mode):
        raise RuntimeError("MDA eval task identity.json must not be a symlink.")
    if not stat.S_ISREG(identity_metadata.st_mode):
        raise RuntimeError("MDA eval task identity.json must be a regular file.")

    try:
        resolved_identity = identity_path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("MDA eval task identity.json could not be resolved.") from error
    if resolved_identity.parent != task_path:
        raise RuntimeError("MDA eval task identity.json must remain inside its task directory.")
    return resolved_identity


def _validate_identity_contents(identity_path: Path) -> None:
    try:
        identity: object = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("MDA eval task identity.json must be valid UTF-8 JSON.") from error
    user = identity.get("user") if isinstance(identity, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(user_id, str) or not user_id.strip():
        raise RuntimeError("MDA eval task identity.json must contain a non-empty string user.id.")


def _validated_identity_path(project_root: Path, task_path_value: object) -> Path | None:
    task_path = _validated_task_path(project_root, task_path_value)
    if task_path is None:
        return None
    identity_path = _validated_identity_file(task_path)
    if identity_path is None:
        return None

    _validate_identity_contents(identity_path)
    return identity_path


def _require_compile_success(stdout: bytes, stderr: bytes, returncode: int) -> None:
    if returncode != 0:
        diagnostic = _process_diagnostic(stdout, stderr, returncode)
        raise RuntimeError(f"MDA eval artifact compile failed: {diagnostic}")


def _trial_identity_key(event: TrialHookEvent) -> tuple[str, str]:
    return (f"{event.trial_name}__agent", str(event.trial_id))


def _wire_trial_identity_hooks(
    job: Job,
    settings: _RuntimeSettings,
    artifact: _ArtifactBinding,
    registered_keys: set[tuple[str, str]],
    registered_artifact_keys: set[tuple[str, str]],
) -> None:
    async def register_identity(event: TrialHookEvent) -> None:
        session_id, context_id = _trial_identity_key(event)
        _clear_trial_identity(session_id, context_id)
        _clear_trial_artifact(session_id, context_id)
        registered_keys.discard((session_id, context_id))
        registered_artifact_keys.discard((session_id, context_id))
        if event.config.agent.import_path != _MDA_AGENT_IMPORT_PATH:
            return

        _register_trial_artifact(
            session_id,
            context_id,
            artifact_path=artifact.artifact_path,
            artifact_sha256=artifact.artifact_sha256,
            graph=artifact.graph,
            model_name=artifact.model_name,
            model_override=artifact.model_override,
            runtime_version=artifact.runtime_version,
        )
        registered_artifact_keys.add((session_id, context_id))

        task = event.config.task
        if getattr(task, "name", None) is not None or getattr(task, "git_url", None) is not None:
            return
        identity_path = _validated_identity_path(settings.project_root, getattr(task, "path", None))
        if identity_path is not None:
            _register_trial_identity(session_id, context_id, identity_path)
            registered_keys.add((session_id, context_id))

    async def clear_identity(event: TrialHookEvent) -> None:
        key = _trial_identity_key(event)
        _clear_trial_identity(*key)
        _clear_trial_artifact(*key)
        registered_keys.discard(key)
        registered_artifact_keys.discard(key)

    job.on_trial_started(register_identity)
    job.on_trial_ended(clear_identity)
    job.on_trial_cancelled(clear_identity)


class MDAJobPlugin(BaseJobPlugin):
    """Compile a fresh MDA eval artifact before Harbor launches any trials."""

    def __init__(self, *, runtime_settings_path: str | Path | None = None) -> None:
        super().__init__()
        self._runtime_settings_path = (
            Path(runtime_settings_path)
            if runtime_settings_path is not None
            else _runtime_settings_path()
        )
        self._registered_identity_keys: set[tuple[str, str]] = set()
        self._registered_artifact_keys: set[tuple[str, str]] = set()
        self._job_id: str | None = None
        self._artifact_path: Path | None = None

    def _clear_registered_identities(self) -> None:
        for key in self._registered_identity_keys:
            _clear_trial_identity(*key)
        self._registered_identity_keys.clear()

    def _clear_registered_artifacts(self) -> None:
        for key in self._registered_artifact_keys:
            _clear_trial_artifact(*key)
        self._registered_artifact_keys.clear()
        if self._job_id is not None:
            _clear_job_artifact(self._job_id)
            self._job_id = None

    async def on_job_start(self, job: Job) -> None:
        self._clear_registered_identities()
        self._clear_registered_artifacts()
        settings = _load_runtime_settings(self._runtime_settings_path)
        _validate_runtime_paths(settings)
        artifact_id = _job_artifact_id(job)
        artifact_path = _artifact_path(settings, artifact_id)
        self._artifact_path = artifact_path
        argv = _compile_argv(job, settings, artifact_id)
        compile_env = os.environ.copy()
        compile_env[_INSTALL_CHANNEL_ENV] = settings.install_channel
        compile_env[_BUNDLED_RUNTIME_PATH_ENV] = str(settings.bundled_runtime_path)
        compile_env[_RUNTIME_VERSION_ENV] = settings.runtime_version

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=compile_env,
            )
        except OSError as error:
            diagnostic = _concise_diagnostic(str(error).strip() or type(error).__name__)
            with suppress(OSError, RuntimeError):
                _remove_generated_artifact(artifact_path)
            self._artifact_path = None
            raise RuntimeError(
                f"Could not launch MDA eval artifact compile: {diagnostic}; "
                "rerun `mda evals init` for this project."
            ) from error

        try:
            try:
                stdout, stderr = await process.communicate()
            except asyncio.CancelledError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                raise

            _require_compile_success(stdout, stderr, process.returncode)

            artifact = _load_compiled_artifact(artifact_path, settings.runtime_version)
            _register_job_artifact(
                artifact_id,
                artifact_path=artifact.artifact_path,
                artifact_sha256=artifact.artifact_sha256,
                graph=artifact.graph,
                model_name=artifact.model_name,
                model_override=artifact.model_override,
                runtime_version=artifact.runtime_version,
            )
            self._job_id = artifact_id
            _wire_trial_identity_hooks(
                job,
                settings,
                artifact,
                self._registered_identity_keys,
                self._registered_artifact_keys,
            )
        except BaseException:
            self._clear_registered_artifacts()
            with suppress(OSError, RuntimeError):
                _remove_generated_artifact(artifact_path)
            self._artifact_path = None
            raise

    async def on_job_end(self, job_result: JobResult) -> None:
        del job_result
        self._clear_registered_identities()
        self._clear_registered_artifacts()
        artifact_path = self._artifact_path
        self._artifact_path = None
        if artifact_path is not None:
            _remove_generated_artifact(artifact_path)
