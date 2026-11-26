"""Build and capture Robin's global image on a disposable builder sandbox."""

import asyncio
import base64
import hashlib
import logging
import shlex
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from openswe.config import ENV, EnvVar
from openswe.sandboxes.configuration import ExecutionConfiguration

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

RefreshStatus = Literal["never", "refreshing", "success", "failed"]
RefreshKind = Literal["full", "update"]
StepStatus = Literal["running", "success", "failed"]
# Room for a setup that prebuilds a repository's compiled output (smithdb's target/), on top of
# the ~24 minutes the global image's setup takes without it.
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 60 * 60
DEFAULT_UPDATE_TIMEOUT_SECONDS = 10 * 60
DEFAULT_CAPTURE_TIMEOUT_SECONDS = 30 * 60
UPDATE_INTERVAL_SECONDS = 60 * 60
BUILDER_DELETE_AFTER_STOP_SECONDS = 10 * 60
REFRESH_LOG_MAX_CHARS = 40_000
REFRESH_LOG_TAIL_CHARS = 6_000
LIVE_LOG_MAX_BYTES = 6_000
LIVE_LOG_READ_TIMEOUT_SECONDS = 10
DEFAULT_SCRIPT_ROOT = "/open-swe/environment"
REPOS_ENV_VARS = ("ROBIN_REPOS", "OPENSWE_REPOS", "OPENSWE_WORKSPACE_REPOS")
SNAPSHOT_TAG = "latest"


class RefreshStep(BaseModel):
    """One stage of an image build."""

    model_config = ConfigDict(extra="ignore")

    label: str
    status: StepStatus = "running"
    started_at: str = ""
    finished_at: str | None = None
    exit_code: int | None = None
    log_path: str | None = None


class RefreshProgress(Protocol):
    async def start_step(self, label: str, *, log_path: str | None = None) -> None: ...

    async def finish_step(
        self, label: str, status: StepStatus, *, exit_code: int | None = None
    ) -> None: ...

    async def publish_builder(self, sandbox_id: str) -> None: ...

    async def settle(
        self, status: RefreshStatus, *, log: str | None = None, error: str | None = None
    ) -> None: ...


@dataclass
class SnapshotBuild:
    definition: ExecutionConfiguration
    base_snapshot_id: str | None
    kind: RefreshKind
    progress: RefreshProgress
    capture: Callable[[str], Awaitable[object]]

    async def run(self) -> dict[str, JsonValue]:
        """Build, validate and capture one pinned definition on a disposable sandbox."""
        from openswe.sandboxes.paths import declared_work_dir

        started = datetime.now(UTC)
        sandbox_id: str | None = None
        log = ""
        step = "boot"
        try:
            await self.progress.start_step(step)
            backend = await self.create_builder_sandbox(self.definition, self.base_snapshot_id)
            sandbox_id = str(backend.id)
            await self.progress.finish_step(step, "success")
            await self.progress.publish_builder(sandbox_id)
            for label, command, timeout in self.scripts_to_run(
                self.definition, self.kind, work_dir=declared_work_dir(backend) or "/workspace"
            ):
                step = label
                await self.progress.start_step(label, log_path=self.script_log_path(label))
                result = await backend.aexecute(command, timeout=timeout)
                log = f"{log}\n--- {label} script ---\n{result.output or ''}".strip()
                if result.exit_code != 0:
                    await self.progress.finish_step(label, "failed", exit_code=result.exit_code)
                    error = f"{label} script exited {result.exit_code}"
                    await self.progress.settle("failed", log=log, error=error)
                    return {
                        "status": "failed",
                        "script": label,
                        "exit_code": result.exit_code,
                        "error": error,
                        "log": log,
                    }
                await self.progress.finish_step(label, "success", exit_code=0)
            step = "capture"
            await self.progress.start_step(step)
            await self.capture(sandbox_id)
            await self.progress.finish_step(step, "success")
        except asyncio.CancelledError:
            logger.warning("Refresh interrupted", extra={"step": step, "sandbox_id": sandbox_id})
            await self.progress.settle("failed", log=log, error="interrupted")
            raise
        except Exception as exc:
            logger.warning("Refresh failed", extra={"step": step}, exc_info=True)
            error = str(exc) or type(exc).__name__
            await self.progress.settle("failed", log=log, error=error)
            return {"status": "failed", "error": error, "log": log}
        finally:
            if sandbox_id:
                await self.release_builder_sandbox(sandbox_id)
        elapsed = int((datetime.now(UTC) - started).total_seconds())
        await self.progress.settle("success", log=log)
        return {"status": "success", "kind": self.kind, "seconds": elapsed, "log": log}

    @staticmethod
    def snapshot_name_prefix() -> str:
        """Prefix for captured snapshot names, so images can be scoped to a deployment."""
        prefix = ENV.EXECUTION_SNAPSHOT_PREFIX.get("").strip()
        if ":" in prefix:
            logger.warning(
                "snapshot prefix contains a colon, which snapshot names may not; "
                "falling back to the default prefix",
                extra={"snapshot_prefix": prefix},
            )
            prefix = ""
        return prefix or "openswe"

    @staticmethod
    def script_root() -> str:
        """Where an image's scripts and their logs live inside a sandbox."""
        return ENV.OPENSWE_SCRIPT_ROOT.get().strip().rstrip("/") or DEFAULT_SCRIPT_ROOT

    @staticmethod
    def script_log_path(label: str) -> str:
        """Canonical log path for a script, inside the sandbox and in the snapshot."""
        return f"{SnapshotBuild.script_root()}/logs/{label}.log"

    @staticmethod
    def script_command(
        script: str, label: str, repos: Sequence[str] = (), *, work_dir: str = "/workspace"
    ) -> str:
        """Shell command that writes one of an image's scripts, runs it, and logs it."""
        root = SnapshotBuild.script_root()
        path = f"{root}/{label}.sh"
        log_path = SnapshotBuild.script_log_path(label)
        encoded = base64.b64encode(script.encode()).decode()
        repositories = shlex.quote(" ".join(repos))
        environment = " ".join(f"{name}={repositories}" for name in REPOS_ENV_VARS)
        commands = []
        if label == "update":
            for repo in repos:
                checkout = shlex.quote(f"{work_dir}/{repo.rsplit('/', 1)[-1]}")
                url = shlex.quote(f"https://github.com/{repo}.git")
                commands.append(
                    f"if [ ! -e {checkout} ] && [ ! -L {checkout} ]; then "
                    f"git clone --quiet -- {url} {checkout} || exit $?; fi"
                )
        commands.append(f"exec bash -x {shlex.quote(path)}")
        invocation = shlex.quote("\n".join(commands))
        return (
            f"mkdir -p {shlex.quote(f'{root}/logs')} "
            f"&& printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)} "
            f"&& {{ {environment} "
            f"GIT_TERMINAL_PROMPT=0 bash -xc {invocation} > {shlex.quote(log_path)} 2>&1; }}; "
            f"rc=$?; cat {shlex.quote(log_path)} 2>/dev/null; exit $rc"
        )

    @staticmethod
    async def capture_sandbox_snapshot(sandbox_id: str, snapshot_name: str, *, timeout: int) -> str:
        """Capture ``sandbox_id`` as ``snapshot_name:latest`` and return the new snapshot id."""
        from openswe.sandboxes.providers.langsmith import (
            capture_snapshot_with_tag,
            get_async_sandbox_client,
        )

        async with get_async_sandbox_client() as client:
            snapshot = await capture_snapshot_with_tag(
                client, sandbox_id, snapshot_name, SNAPSHOT_TAG, timeout=timeout
            )
        logger.info(
            "Captured snapshot",
            extra={
                "snapshot_id": str(snapshot.id),
                "snapshot_name": snapshot_name,
                "snapshot_tag": SNAPSHOT_TAG,
                "sandbox_id": sandbox_id,
            },
        )
        return str(snapshot.id)

    @staticmethod
    def seconds(var: EnvVar, default: int) -> int:
        """A positive timeout from ``var``, else ``default``."""
        seconds = var.get_int(default)
        return seconds if seconds > 0 else default

    @staticmethod
    def capture_timeout() -> int:
        return SnapshotBuild.seconds(
            ENV.EXECUTION_CAPTURE_TIMEOUT_SECONDS, DEFAULT_CAPTURE_TIMEOUT_SECONDS
        )

    @staticmethod
    def daily_schedule(slug: str) -> str:
        """Daily cron expression, staggered per image to spread the rebuilds."""
        digest = int(hashlib.sha256(slug.encode()).hexdigest(), 16)
        return f"{digest % 60} {3 + (digest // 60) % 3} * * *"

    @staticmethod
    async def release_builder_sandbox(sandbox_id: str) -> None:
        """Stop the builder so the platform reclaims it on its delete-after-stop window."""
        from openswe.sandboxes.providers.langsmith import get_async_sandbox_client

        try:
            async with get_async_sandbox_client() as client:
                await client.stop_sandbox(sandbox_id)
        except Exception:
            logger.warning(
                "Failed to stop builder sandbox", extra={"sandbox_id": sandbox_id}, exc_info=True
            )

    @staticmethod
    async def create_builder_sandbox(
        record: ExecutionConfiguration, snapshot_id: str | None
    ) -> SandboxBackendProtocol:
        from openswe.github.sandbox_access import installation_token
        from openswe.sandboxes.providers.langsmith import (
            configure_sandbox_proxy,
            create_langsmith_sandbox,
            get_sandbox_proxy_config,
        )

        access = await installation_token()
        create_params = {
            **record.sandbox_create_params(),
            "delete_after_stop_seconds": BUILDER_DELETE_AFTER_STOP_SECONDS,
        }
        backend = await create_langsmith_sandbox(
            snapshot_id=snapshot_id,
            create_params=create_params,
            **record.sandbox_resources(),
        )
        await configure_sandbox_proxy(
            backend.id, access.token, base_proxy_config=get_sandbox_proxy_config(create_params)
        )
        return backend

    @staticmethod
    def scripts_to_run(
        record: ExecutionConfiguration, kind: RefreshKind, *, work_dir: str = "/workspace"
    ) -> list[tuple[str, str, int]]:
        """The scripts a refresh runs, in order, as ``(label, command, timeout)``."""
        steps: list[tuple[str, str, int]] = []
        if kind == "full":
            steps.append(
                (
                    "setup",
                    SnapshotBuild.script_command(record.setup_script, "setup", record.repos),
                    SnapshotBuild.seconds(
                        ENV.EXECUTION_REFRESH_TIMEOUT_SECONDS, DEFAULT_SCRIPT_TIMEOUT_SECONDS
                    ),
                )
            )
        if record.update_script:
            steps.append(
                (
                    "update",
                    SnapshotBuild.script_command(
                        record.update_script, "update", record.repos, work_dir=work_dir
                    ),
                    SnapshotBuild.seconds(
                        ENV.EXECUTION_UPDATE_TIMEOUT_SECONDS, DEFAULT_UPDATE_TIMEOUT_SECONDS
                    ),
                )
            )
        return steps

    @staticmethod
    async def read_builder_log(sandbox_id: str, path: str) -> str | None:
        """Tail a script's live ``bash -x`` trace off the builder, or ``None``."""
        from openswe.sandboxes.providers.registry import connect_existing_sandbox

        try:
            backend = await connect_existing_sandbox(sandbox_id)
            result = await backend.aexecute(
                f"tail -c {LIVE_LOG_MAX_BYTES} {shlex.quote(path)} 2>/dev/null",
                timeout=LIVE_LOG_READ_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug(
                "Builder sandbox is not readable", extra={"sandbox_id": sandbox_id}, exc_info=True
            )
            return None
        return (result.output or "").strip() or None
