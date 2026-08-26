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

import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.store import TypedStore, now_iso

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


def _stale_build_seconds() -> int:
    raw = os.environ.get("REPO_SNAPSHOT_STALE_BUILD_SECONDS")
    if not raw:
        return DEFAULT_STALE_BUILD_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALE_BUILD_SECONDS
    return max(value, 0)


class RepoSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str
    owner: str = ""
    name: str = ""
    dockerfile: str = ""
    snapshot_id: str | None = None
    snapshot_name: str | None = None
    status: BuildStatus = "none"
    status_message: str | None = None
    build_log: str | None = None
    fs_capacity_bytes: int = DEFAULT_BUILD_FS_CAPACITY_BYTES
    vcpus: int = DEFAULT_BUILD_VCPUS
    mem_bytes: int = DEFAULT_BUILD_MEM_BYTES
    target: str | None = None
    build_args: dict[str, str] | None = None
    build_started_at: str | None = None
    last_built_at: str | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def seed(cls, full_name: str, created_by: str = "") -> "RepoSnapshot":
        owner, _, name = full_name.partition("/")
        now = now_iso()
        return cls(
            full_name=full_name,
            owner=owner,
            name=name,
            dockerfile=generate_dockerfile_template(full_name),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    @property
    def build_is_stale(self) -> bool:
        """Whether a ``building`` record has been stuck long enough to override."""
        if self.status != "building":
            return False
        started_at = _parse_iso(self.build_started_at)
        if started_at is None:
            return True
        return (datetime.now(UTC) - started_at).total_seconds() > _stale_build_seconds()


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class RepoSnapshotStore(TypedStore[RepoSnapshot]):
    """Repo snapshots, keyed by the normalized ``owner/repo``."""

    def __init__(self) -> None:
        super().__init__(REPO_SNAPSHOTS_NAMESPACE, RepoSnapshot)

    async def get(self, key: str) -> RepoSnapshot | None:
        return await super().get(normalize_repo_full_name(key))

    async def delete(self, key: str) -> None:
        await super().delete(normalize_repo_full_name(key))

    async def list_all(self) -> list[RepoSnapshot]:
        records = await self.search_all()
        records.sort(key=lambda record: record.full_name)
        return records

    async def save(self, record: RepoSnapshot) -> RepoSnapshot:
        record.updated_at = now_iso()
        return await self.put(record.full_name, record)

    async def create(self, full_name: str, created_by: str) -> RepoSnapshot:
        full_name = normalize_repo_full_name(full_name)
        existing = await self.get(full_name)
        if existing:
            return existing
        return await self.put(full_name, RepoSnapshot.seed(full_name, created_by))

    async def apply_update(self, full_name: str, update: RepoSnapshotUpdate) -> RepoSnapshot:
        record = await self.get(full_name) or RepoSnapshot.seed(normalize_repo_full_name(full_name))
        record.dockerfile = update.dockerfile
        record.target = update.target
        record.build_args = update.build_args
        if update.fs_capacity_bytes is not None:
            record.fs_capacity_bytes = update.fs_capacity_bytes
        if update.vcpus is not None:
            record.vcpus = update.vcpus
        if update.mem_bytes is not None:
            record.mem_bytes = update.mem_bytes
        return await self.save(record)

    async def mark_building(self, full_name: str) -> RepoSnapshot:
        record = await self.get(full_name)
        if record is None:
            raise ValueError(f"no repo snapshot record for {full_name}")
        record.status = "building"
        record.status_message = None
        record.build_log = None
        record.build_started_at = now_iso()
        return await self.save(record)

    async def mark_ready(
        self, full_name: str, *, snapshot_id: str, snapshot_name: str, build_log: str
    ) -> None:
        record = await self.get(full_name)
        if record is None:
            return
        record.status = "ready"
        record.status_message = None
        record.build_started_at = None
        record.snapshot_id = snapshot_id
        record.snapshot_name = snapshot_name
        record.build_log = build_log
        record.last_built_at = now_iso()
        await self.save(record)

    async def mark_failed(self, full_name: str, message: str) -> None:
        record = await self.get(full_name)
        if record is None:
            return
        record.status = "failed"
        record.status_message = message
        record.build_started_at = None
        await self.save(record)


REPO_SNAPSHOTS = RepoSnapshotStore()


async def resolve_repo_snapshot_id(owner: str | None, name: str | None) -> str | None:
    """Return a repo's ready snapshot id, or ``None`` to fall back to the default.

    Fail-soft on purpose: this runs while a sandbox is being created, and a
    lookup failure must fall back to the configured
    ``DEFAULT_SANDBOX_SNAPSHOT_ID`` rather than fail the run.
    """
    if not owner or not name:
        return None
    try:
        record = await REPO_SNAPSHOTS.get(f"{owner}/{name}")
    except Exception:
        logger.warning("repo snapshot lookup failed for %s/%s", owner, name, exc_info=True)
        return None
    if not record or record.status != "ready":
        return None
    return record.snapshot_id or None


def _build_snapshot_sync(record: RepoSnapshot, snapshot_name: str) -> tuple[str, str]:
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
            dockerfile_path.write_text(record.dockerfile)
            snapshot = client.create_snapshot_from_dockerfile(
                snapshot_name,
                dockerfile="Dockerfile",
                fs_capacity_bytes=record.fs_capacity_bytes,
                context=context_dir,
                build_args=record.build_args or None,
                target=record.target or None,
                on_build_log=_on_log,
                vcpus=record.vcpus,
                mem_bytes=record.mem_bytes,
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
    record = await REPO_SNAPSHOTS.get(full_name)
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
        await REPO_SNAPSHOTS.mark_failed(full_name, str(e)[:1000])
        return

    await REPO_SNAPSHOTS.mark_ready(
        full_name,
        snapshot_id=snapshot_id,
        snapshot_name=snapshot_name,
        build_log=log_tail,
    )
    logger.info("Built snapshot %s for repo %s", snapshot_id, full_name)
