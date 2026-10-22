"""Harbor adapter for Managed Deep Agents eval trials.

Harbor loads the agent via ``mda_harbor.agent:ManagedDeepAgent``. Keep this
package init free of Harbor imports so the package can be imported without
``harbor`` installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["ManagedDeepAgent"]

_TRIAL_IDENTITY_PATHS: dict[tuple[str, str], Path] = {}


@dataclass(frozen=True)
class _ArtifactBinding:
    artifact_path: Path
    artifact_sha256: str
    graph: str
    model_name: str | None
    model_override: str | None
    runtime_version: str


_JOB_ARTIFACTS: dict[str, _ArtifactBinding] = {}
_TRIAL_ARTIFACTS: dict[tuple[str, str], _ArtifactBinding] = {}


def _trial_identity_key(session_id: object, context_id: object) -> tuple[str, str]:
    return (str(session_id), str(context_id))


def _register_trial_identity(
    session_id: object,
    context_id: object,
    identity_path: str | Path,
) -> None:
    _TRIAL_IDENTITY_PATHS[_trial_identity_key(session_id, context_id)] = Path(identity_path)


def _trial_identity_path(session_id: object, context_id: object) -> Path | None:
    return _TRIAL_IDENTITY_PATHS.get(_trial_identity_key(session_id, context_id))


def _clear_trial_identity(session_id: object, context_id: object) -> None:
    _TRIAL_IDENTITY_PATHS.pop(_trial_identity_key(session_id, context_id), None)


def _artifact_binding(
    *,
    artifact_path: str | Path,
    artifact_sha256: str,
    graph: str,
    model_name: str | None,
    model_override: str | None = None,
    runtime_version: str,
) -> _ArtifactBinding:
    values = {
        "artifact_sha256": artifact_sha256,
        "graph": graph,
        "runtime_version": runtime_version,
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("MDA artifact binding metadata must use non-empty strings")
    for field, value in (("model_name", model_name), ("model_override", model_override)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"MDA artifact binding {field} must be null or a non-empty string")
    if model_override is not None and model_override != model_name:
        raise ValueError("MDA artifact model_override must match model_name")
    return _ArtifactBinding(
        artifact_path=Path(artifact_path).expanduser().resolve(),
        artifact_sha256=artifact_sha256,
        graph=graph,
        model_name=model_name,
        model_override=model_override,
        runtime_version=runtime_version,
    )


def _register_job_artifact(
    job_id: object,
    *,
    artifact_path: str | Path,
    artifact_sha256: str,
    graph: str,
    model_name: str | None,
    model_override: str | None = None,
    runtime_version: str,
) -> _ArtifactBinding:
    binding = _artifact_binding(
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        graph=graph,
        model_name=model_name,
        model_override=model_override,
        runtime_version=runtime_version,
    )
    _JOB_ARTIFACTS[str(job_id)] = binding
    return binding


def _job_artifact(job_id: object) -> _ArtifactBinding | None:
    return _JOB_ARTIFACTS.get(str(job_id))


def _clear_job_artifact(job_id: object) -> None:
    _JOB_ARTIFACTS.pop(str(job_id), None)


def _register_trial_artifact(
    session_id: object,
    context_id: object,
    *,
    artifact_path: str | Path,
    artifact_sha256: str,
    graph: str,
    model_name: str | None,
    model_override: str | None = None,
    runtime_version: str,
) -> _ArtifactBinding:
    binding = _artifact_binding(
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        graph=graph,
        model_name=model_name,
        model_override=model_override,
        runtime_version=runtime_version,
    )
    _TRIAL_ARTIFACTS[_trial_identity_key(session_id, context_id)] = binding
    return binding


def _trial_artifact(session_id: object, context_id: object) -> _ArtifactBinding | None:
    return _TRIAL_ARTIFACTS.get(_trial_identity_key(session_id, context_id))


def _clear_trial_artifact(session_id: object, context_id: object) -> None:
    _TRIAL_ARTIFACTS.pop(_trial_identity_key(session_id, context_id), None)


def __getattr__(name: str) -> object:
    if name == "ManagedDeepAgent":
        from mda_harbor.agent import ManagedDeepAgent  # noqa: PLC0415

        return ManagedDeepAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
