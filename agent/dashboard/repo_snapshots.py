"""Per-repository sandbox snapshots built from custom Dockerfiles.

Each record holds an admin-authored Dockerfile (edited in the dashboard) and the
id of the LangSmith snapshot most recently built from it. When a run targets a
repo that has a ``ready`` snapshot, the sandbox boots from it instead of the
global ``DEFAULT_SANDBOX_SNAPSHOT_ID``. Repos without a ready snapshot always
fall back to that configured default, so this is purely additive.

Builds run server-side via ``SandboxClient.create_snapshot_from_dockerfile``,
which uploads the Dockerfile context to a throwaway LangSmith builder sandbox,
runs BuildKit there, and captures the result. Nothing is executed on the host.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from langgraph_sdk import get_client
from pydantic import BaseModel, Field, field_validator

from .review_styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

REPO_SNAPSHOTS_NAMESPACE: list[str] = ["repo_snapshots"]

BuildStatus = Literal["none", "building", "ready", "failed"]


class RepoSnapshotConfigError(RuntimeError):
    pass


DOCKERFILE_MAX_CHARS = 100_000
BUILD_LOG_MAX_CHARS = 20_000

# Build sizing defaults. The builder sandbox must hold the build context, the
# intermediate layers, and the final image, so default generously.
DEFAULT_BUILD_FS_CAPACITY_BYTES = 32 * 1024**3
DEFAULT_BUILD_VCPUS = 2
DEFAULT_BUILD_MEM_BYTES = 8 * 1024**3
DEFAULT_BUILD_TIMEOUT_SECONDS = 30 * 60
DEFAULT_STALE_BUILD_SECONDS = 6 * 60 * 60

_MIN_FS_CAPACITY_BYTES = 1 * 1024**3
_MAX_FS_CAPACITY_BYTES = 128 * 1024**3
_MIN_MEM_BYTES = 1 * 1024**3
_MAX_MEM_BYTES = 64 * 1024**3
_MIN_VCPUS = 1
_MAX_VCPUS = 16


def _default_base_image() -> str:
    """Base image used to seed generated Dockerfile templates."""
    image = os.environ.get("REPO_SNAPSHOT_BASE_IMAGE", "").strip()
    if not image:
        raise RepoSnapshotConfigError(
            "REPO_SNAPSHOT_BASE_IMAGE must be set to the published Open SWE sandbox image"
        )
    return image


def generate_dockerfile_template(full_name: str) -> str:
    """Return a starter Dockerfile for a repo, extending the Open SWE base image."""
    base = _default_base_image()
    return (
        f"# Dockerfile for {full_name}\n"
        "#\n"
        "# This image becomes the sandbox snapshot for runs targeting this repo.\n"
        "# It MUST keep the tools Open SWE relies on (git, gh, the language\n"
        "# toolchain, sfw), so extend the Open SWE base image rather than starting\n"
        "# from a bare OS image. Add only repo-specific dependencies below.\n"
        f"FROM {base}\n"
        "\n"
        "# Example: pre-install system + project dependencies so they are baked\n"
        "# into the snapshot and runs start with everything already available.\n"
        "# RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
        "#     postgresql-client \\\n"
        "#     && rm -rf /var/lib/apt/lists/*\n"
        "\n"
        "WORKDIR /workspace\n"
    )


class RepoSnapshotCreate(BaseModel):
    full_name: str = Field(..., description="GitHub repo in owner/name form")

    @field_validator("full_name", mode="before")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        return normalize_repo_full_name(v)


class RepoSnapshotUpdate(BaseModel):
    dockerfile: str = Field(default="")
    fs_capacity_bytes: int | None = None
    vcpus: int | None = None
    mem_bytes: int | None = None
    target: str | None = None
    build_args: dict[str, str] | None = None

    @field_validator("dockerfile")
    @classmethod
    def _dockerfile_len(cls, v: str) -> str:
        if len(v) > DOCKERFILE_MAX_CHARS:
            raise ValueError(f"dockerfile must be at most {DOCKERFILE_MAX_CHARS} characters")
        return v

    @field_validator("fs_capacity_bytes")
    @classmethod
    def _fs_capacity(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not _MIN_FS_CAPACITY_BYTES <= v <= _MAX_FS_CAPACITY_BYTES:
            raise ValueError("fs_capacity_bytes out of range")
        return v

    @field_validator("vcpus")
    @classmethod
    def _vcpus(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not _MIN_VCPUS <= v <= _MAX_VCPUS:
            raise ValueError("vcpus out of range")
        return v

    @field_validator("mem_bytes")
    @classmethod
    def _mem_bytes(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not _MIN_MEM_BYTES <= v <= _MAX_MEM_BYTES:
            raise ValueError("mem_bytes out of range")
        return v


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _stale_build_seconds() -> int:
    raw = os.environ.get("REPO_SNAPSHOT_STALE_BUILD_SECONDS")
    if not raw:
        return DEFAULT_STALE_BUILD_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALE_BUILD_SECONDS
    return max(value, 0)


def is_repo_snapshot_build_stale(record: dict[str, Any]) -> bool:
    if record.get("status") != "building":
        return False
    started_at = _parse_iso(record.get("build_started_at"))
    if started_at is None:
        return True
    return (datetime.now(UTC) - started_at).total_seconds() > _stale_build_seconds()


async def _get_value(key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(REPO_SNAPSHOTS_NAMESPACE, key)
    except Exception as e:  # noqa: BLE001
        logger.debug("store get_item failed for %s: %s", key, e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _default_record(full_name: str, created_by: str) -> dict[str, Any]:
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "owner": owner,
        "name": name,
        "dockerfile": generate_dockerfile_template(full_name),
        "snapshot_id": None,
        "snapshot_name": None,
        "status": "none",
        "status_message": None,
        "build_log": None,
        "fs_capacity_bytes": DEFAULT_BUILD_FS_CAPACITY_BYTES,
        "vcpus": DEFAULT_BUILD_VCPUS,
        "mem_bytes": DEFAULT_BUILD_MEM_BYTES,
        "target": None,
        "build_args": None,
        "build_started_at": None,
        "last_built_at": None,
        "created_by": created_by,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


async def get_repo_snapshot(full_name: str) -> dict[str, Any] | None:
    return await _get_value(normalize_repo_full_name(full_name))


async def list_repo_snapshots() -> list[dict[str, Any]]:
    try:
        result = await _client().store.search_items(REPO_SNAPSHOTS_NAMESPACE, limit=1000)
    except Exception as e:  # noqa: BLE001
        logger.debug("store search_items failed for repo_snapshots: %s", e)
        return []
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    out.sort(key=lambda r: r.get("full_name", ""))
    return out


async def create_repo_snapshot(full_name: str, created_by: str) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    existing = await get_repo_snapshot(full_name)
    if existing:
        return existing
    value = _default_record(full_name, created_by)
    await _client().store.put_item(REPO_SNAPSHOTS_NAMESPACE, full_name, value)
    return value


async def update_repo_snapshot(full_name: str, update: RepoSnapshotUpdate) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    existing = await get_repo_snapshot(full_name) or _default_record(full_name, "")
    value = {**existing, "dockerfile": update.dockerfile, "updated_at": _now_iso()}
    if update.fs_capacity_bytes is not None:
        value["fs_capacity_bytes"] = update.fs_capacity_bytes
    if update.vcpus is not None:
        value["vcpus"] = update.vcpus
    if update.mem_bytes is not None:
        value["mem_bytes"] = update.mem_bytes
    value["target"] = update.target
    value["build_args"] = update.build_args
    await _client().store.put_item(REPO_SNAPSHOTS_NAMESPACE, full_name, value)
    return value


async def delete_repo_snapshot(full_name: str) -> bool:
    full_name = normalize_repo_full_name(full_name)
    existing = await get_repo_snapshot(full_name)
    if not existing:
        return False
    try:
        await _client().store.delete_item(REPO_SNAPSHOTS_NAMESPACE, full_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to delete repo snapshot %s: %s", full_name, e)
        return False
    return True


async def mark_repo_snapshot_building(full_name: str) -> dict[str, Any]:
    """Set a repo snapshot's status to ``building`` and return the record."""
    full_name = normalize_repo_full_name(full_name)
    existing = await get_repo_snapshot(full_name)
    if existing is None:
        raise ValueError(f"no repo snapshot record for {full_name}")
    value = {
        **existing,
        "status": "building",
        "status_message": None,
        "build_log": None,
        "build_started_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await _client().store.put_item(REPO_SNAPSHOTS_NAMESPACE, full_name, value)
    return value


async def resolve_repo_snapshot_id(owner: str | None, name: str | None) -> str | None:
    """Return a repo's ready snapshot id, or ``None`` to fall back to the default.

    Never raises: any lookup failure resolves to ``None`` so sandbox creation
    falls back to the configured ``DEFAULT_SANDBOX_SNAPSHOT_ID``.
    """
    if not owner or not name:
        return None
    try:
        record = await _get_value(f"{owner}/{name}")
    except Exception:  # noqa: BLE001
        logger.debug("repo snapshot lookup failed for %s/%s", owner, name, exc_info=True)
        return None
    if not record or record.get("status") != "ready":
        return None
    snapshot_id = record.get("snapshot_id")
    return snapshot_id if isinstance(snapshot_id, str) and snapshot_id else None


async def _set_status(
    full_name: str,
    status: BuildStatus,
    *,
    status_message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    existing = await get_repo_snapshot(full_name)
    if existing is None:
        return
    value = {
        **existing,
        "status": status,
        "status_message": status_message,
        "updated_at": _now_iso(),
    }
    if status != "building":
        value["build_started_at"] = None
    if extra:
        value.update(extra)
    await _client().store.put_item(REPO_SNAPSHOTS_NAMESPACE, full_name, value)


def _build_snapshot_sync(record: dict[str, Any], snapshot_name: str) -> tuple[str, str]:
    """Build a snapshot from the record's Dockerfile. Runs in a worker thread.

    Returns ``(snapshot_id, build_log_tail)``. Raises on build failure.
    """
    from langsmith.sandbox import SandboxClient

    from agent.integrations.langsmith import _get_sandbox_api_endpoint, _get_sandbox_api_key

    api_key = _get_sandbox_api_key()
    if not api_key:
        raise RuntimeError("LANGSMITH_API_KEY is not configured")

    logs: list[str] = []

    def _on_log(line: str) -> None:
        logs.append(line)

    timeout = int(
        os.environ.get("REPO_SNAPSHOT_BUILD_TIMEOUT_SECONDS", DEFAULT_BUILD_TIMEOUT_SECONDS)
    )
    client = SandboxClient(api_key=api_key, api_endpoint=_get_sandbox_api_endpoint())
    try:
        with tempfile.TemporaryDirectory(prefix="openswe-snapshot-") as context_dir:
            dockerfile_path = Path(context_dir) / "Dockerfile"
            dockerfile_path.write_text(record.get("dockerfile") or "")
            build_args = (
                record.get("build_args") if isinstance(record.get("build_args"), dict) else None
            )
            target = record.get("target") if isinstance(record.get("target"), str) else None
            snapshot = client.create_snapshot_from_dockerfile(
                snapshot_name,
                dockerfile="Dockerfile",
                fs_capacity_bytes=int(
                    record.get("fs_capacity_bytes") or DEFAULT_BUILD_FS_CAPACITY_BYTES
                ),
                context=context_dir,
                build_args=build_args or None,
                target=target or None,
                on_build_log=_on_log,
                vcpus=int(record.get("vcpus") or DEFAULT_BUILD_VCPUS),
                mem_bytes=int(record.get("mem_bytes") or DEFAULT_BUILD_MEM_BYTES),
                timeout=timeout,
            )
    finally:
        client.close()

    log_tail = "".join(logs)[-BUILD_LOG_MAX_CHARS:]
    return snapshot.id, log_tail


async def run_snapshot_build(full_name: str) -> None:
    """Build (or rebuild) the snapshot for a repo and persist the result.

    Intended to run as a FastAPI background task. The status is set to
    ``building`` before kicking off the (blocking) build in a worker thread.
    """
    import asyncio

    full_name = normalize_repo_full_name(full_name)
    record = await get_repo_snapshot(full_name)
    if record is None:
        logger.warning("Cannot build snapshot for %s: no record", full_name)
        return

    owner, name = full_name.split("/", 1)
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    snapshot_name = f"openswe-{owner}-{name}-{timestamp}".replace("/", "-").lower()

    try:
        snapshot_id, log_tail = await asyncio.to_thread(_build_snapshot_sync, record, snapshot_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("Snapshot build failed for %s: %s", full_name, e, exc_info=True)
        await _set_status(
            full_name,
            "failed",
            status_message=str(e)[:1000],
        )
        return

    await _set_status(
        full_name,
        "ready",
        status_message=None,
        extra={
            "snapshot_id": snapshot_id,
            "snapshot_name": snapshot_name,
            "build_log": log_tail,
            "last_built_at": _now_iso(),
        },
    )
    logger.info("Built snapshot %s for repo %s", snapshot_id, full_name)
