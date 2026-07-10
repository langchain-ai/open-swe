"""Lightning AI sandbox backend integration.

Thin factory around ``langchain-lightning`` (same pattern as e2b/daytona/modal).

Auth (first match wins):
  - ``LIGHTNING_SANDBOX_API_KEY``
  - ``LIGHTNING_API_KEY``

Optional env:
  - ``LIGHTNING_CLOUD_URL`` — API host (default ``https://lightning.ai``)
  - ``LIGHTNING_SANDBOX_INSTANCE_TYPE`` — default ``cpu-1``
  - ``LIGHTNING_SANDBOX_RUNTIME`` — default ``python313`` (ships python3)
  - ``LIGHTNING_SANDBOX_IMAGE`` — custom OCI image (mutually exclusive with runtime)
  - ``LIGHTNING_SANDBOX_TIMEOUT_MS`` — sandbox lifetime in milliseconds
  - ``LIGHTNING_SANDBOX_STORAGE_GB`` — writable disk size (CPU only)
  - ``LIGHTNING_SANDBOX_NETWORK_POLICY`` — ``allow-all`` / ``deny-all`` / etc.
  - ``LIGHTNING_SANDBOX_PERSISTENT`` — ``true``/``false`` (default ``true``)
  - ``LIGHTNING_SANDBOX_WORKDIR`` — command cwd (default ``/workspace``)
"""

from __future__ import annotations

import os
from typing import Any

from langchain_lightning import (
    LightningSandbox,
    ensure_workdir,
    resume_if_needed,
)
from lightning_sdk.sandbox import Sandbox, SandboxConfig

DEFAULT_WORKDIR = "/workspace"
DEFAULT_INSTANCE_TYPE = "cpu-1"
DEFAULT_RUNTIME = "python313"
DEFAULT_NETWORK_POLICY = "allow-all"


def _api_key_from_env() -> str | None:
    return os.getenv("LIGHTNING_SANDBOX_API_KEY") or os.getenv("LIGHTNING_API_KEY") or None


def _truthy(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _sandbox_config() -> SandboxConfig:
    api_key = _api_key_from_env()
    if not api_key:
        raise ValueError(
            "LIGHTNING_API_KEY or LIGHTNING_SANDBOX_API_KEY environment variable is required"
        )
    base_url = os.getenv("LIGHTNING_CLOUD_URL") or None
    return SandboxConfig(api_key=api_key, base_url=base_url)


def create_lightning_sandbox(sandbox_id: str | None = None) -> LightningSandbox:
    """Create or reconnect to a Lightning AI sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect/resume.
            If None, creates a new sandbox with the ``python313`` runtime.

    Returns:
        LightningSandbox implementing SandboxBackendProtocol.
    """
    client = Sandbox(config=_sandbox_config())
    workdir = os.getenv("LIGHTNING_SANDBOX_WORKDIR", DEFAULT_WORKDIR).strip() or DEFAULT_WORKDIR

    if sandbox_id:
        sandbox = resume_if_needed(client.get(sandbox_id))
    else:
        instance_type = (
            os.getenv("LIGHTNING_SANDBOX_INSTANCE_TYPE", DEFAULT_INSTANCE_TYPE).strip()
            or DEFAULT_INSTANCE_TYPE
        )
        network_policy = (
            os.getenv("LIGHTNING_SANDBOX_NETWORK_POLICY", DEFAULT_NETWORK_POLICY).strip()
            or DEFAULT_NETWORK_POLICY
        )
        persistent = _truthy(os.getenv("LIGHTNING_SANDBOX_PERSISTENT"), default=True)
        timeout_ms = _optional_int("LIGHTNING_SANDBOX_TIMEOUT_MS")
        storage_gb = _optional_int("LIGHTNING_SANDBOX_STORAGE_GB")
        runtime = (os.getenv("LIGHTNING_SANDBOX_RUNTIME") or "").strip() or None
        image = (os.getenv("LIGHTNING_SANDBOX_IMAGE") or "").strip() or None
        if runtime and image:
            raise ValueError(
                "Set only one of LIGHTNING_SANDBOX_RUNTIME and LIGHTNING_SANDBOX_IMAGE"
            )

        create_kwargs: dict[str, Any] = {
            "name": f"open-swe-{os.getpid()}",
            "instance_type": instance_type,
            "persistent": persistent,
            "network_policy": network_policy,
        }
        if timeout_ms is not None:
            create_kwargs["timeout"] = timeout_ms
        if storage_gb is not None:
            create_kwargs["storage_gb"] = storage_gb
        if image is not None:
            create_kwargs["image"] = image
        else:
            create_kwargs["runtime"] = runtime or DEFAULT_RUNTIME

        sandbox = client.create(**create_kwargs)

    ensure_workdir(sandbox, workdir)
    return LightningSandbox(sandbox=sandbox, workdir=workdir)
