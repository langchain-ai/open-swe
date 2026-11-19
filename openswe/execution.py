"""Immutable server-resolved execution profiles named by signed run tokens."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS
from openswe.dashboard.settings_records import SettingsRecords
from openswe.database import postgres
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions
from openswe.utils.json_types import JsonObject
from openswe.utils.time import parse_iso
from openswe.workspaces.store import load_workspace

_profile: ContextVar[ExecutionProfile | None] = ContextVar("execution_profile", default=None)
_mode: ContextVar[bool | None] = ContextVar("execution_defaults_mode", default=None)
_records = SettingsRecords("execution")


class SandboxAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReplacementPin(SandboxAuthority):
    invocation_id: str = Field(min_length=1)


class PendingReplacement(ReplacementPin):
    pending: bool = True
    run_id: str | None = None

    def pin(self) -> ReplacementPin:
        return ReplacementPin(execution=self.execution, invocation_id=self.invocation_id)


class ExecutionProfile(BaseModel):
    """The instructions and creation authority a run started with, without scripts or logs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uses_defaults: bool = False
    create: LangSmithCreateOptions
    name: str | None = None
    instructions: str = ""
    repos: list[str] = Field(default_factory=list)
    refresh_workspace: str | None = None
    captured_at: str | None = None
    refresh_enabled: bool = False
    replacement: ReplacementPin | None = None

    @classmethod
    async def resolve(cls, workspace_slug: str | None, *, thread_id: str | None = None) -> Self:
        from openswe.sandboxes.lifecycle import SandboxCreateConfig

        defaults = await EXECUTION_DEFAULTS.load()
        pending = await SANDBOX_EXECUTION.pending(thread_id) if thread_id else None
        replacement = pending.pin() if pending else None
        if defaults.enabled:
            from openswe.execution_images import EXECUTION_IMAGES

            image = await EXECUTION_IMAGES.state()
            return cls(
                uses_defaults=True,
                create=SandboxCreateConfig.from_defaults(defaults).langsmith_options(),
                instructions=defaults.prompt,
                repos=defaults.repos,
                captured_at=image.captured_at(defaults.snapshot_id),
                refresh_enabled=bool(defaults.update_script),
                replacement=replacement,
            )
        workspace = await load_workspace(workspace_slug)
        config = await SandboxCreateConfig.legacy(workspace_slug)
        return cls(
            create=config.langsmith_options(),
            name=workspace.name if workspace else None,
            instructions=(workspace.instructions or "") if workspace else "",
            repos=workspace.repos if workspace else [],
            refresh_workspace=config.workspace.slug if config.workspace else None,
            replacement=replacement,
        )

    def stale(self, *, interval_seconds: int = 8 * 60 * 60) -> bool:
        if not self.uses_defaults or not self.refresh_enabled or not self.create.snapshot_id:
            return False
        captured = parse_iso(self.captured_at)
        return (
            captured is None or (datetime.now(UTC) - captured).total_seconds() >= interval_seconds
        )

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    async def keep(self) -> str:
        digest = self.digest()
        key = f"run:{digest}"
        if await _records.get(key) is None:
            await _records.put(key, self.model_dump(mode="json"))
        return digest

    @classmethod
    async def kept(cls, digest: str) -> Self:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid execution profile reference")
        data = await _records.get(f"run:{digest}")
        if data is None:
            raise LookupError("The run's execution profile is no longer available")
        profile = cls.model_validate(data)
        if profile.digest() != digest:
            raise ValueError("The run's execution profile has changed")
        return profile

    @classmethod
    def current(cls) -> ExecutionProfile | None:
        return _profile.get()

    @classmethod
    async def use_defaults(cls, *, session: AsyncSession | None = None) -> bool:
        pinned = _mode.get()
        return (
            pinned if pinned is not None else await EXECUTION_DEFAULTS.is_enabled(session=session)
        )

    @classmethod
    @contextmanager
    def bind(cls, profile: ExecutionProfile | None) -> Iterator[None]:
        profile_token = _profile.set(profile)
        mode_token = _mode.set(profile.uses_defaults if profile else False)
        try:
            yield
        finally:
            _mode.reset(mode_token)
            _profile.reset(profile_token)

    def workspace_prompt(self) -> str:
        from openswe.prompt import SystemPrompt

        return SystemPrompt.execution_section(self.name, self.instructions, self.repos)


class SandboxExecutionBindings:
    """Private authority records retained across token expiry and thread deletion, without GC."""

    async def bound(self, thread_id: str, sandbox_id: str) -> ExecutionProfile | None:
        data = await _records.get(f"sandbox:{thread_id}:{sandbox_id}")
        if not data:
            return None
        pin = SandboxAuthority.model_validate(data)
        return await ExecutionProfile.kept(pin.execution)

    async def pending(self, thread_id: str) -> PendingReplacement | None:
        data = await _records.get(f"replacement:{thread_id}")
        if not data:
            return None
        pending = PendingReplacement.model_validate(data)
        return pending if pending.pending else None

    async def request(
        self,
        thread_id: str,
        profile: ExecutionProfile,
        invocation_id: str,
        metadata: JsonObject,
        *,
        run_id: str | None = None,
    ) -> ReplacementPin:
        from openswe.threads.runtimes import RUNTIMES
        from openswe.threads.store import THREADS

        recoverable = await self.pending(thread_id)
        if recoverable is not None and recoverable.invocation_id != invocation_id:
            owner = (
                await RUNTIMES.mda_runtime.run(thread_id, recoverable.run_id)
                if recoverable.run_id is not None
                else None
            )
            if owner is None or owner.active:
                raise ValueError("The previous sandbox replacement has not completed")
        pin = ReplacementPin(execution=await profile.keep(), invocation_id=invocation_id)
        key = f"replacement:{thread_id}"
        async with postgres.session() as session:
            data = await _records.lock(key, session=session)
            previous = PendingReplacement.model_validate(data) if data else None
            if previous is not None and previous.invocation_id == invocation_id:
                if previous.pin() != pin:
                    raise ValueError("A different replacement requires a new run")
                publish = previous.pending
            else:
                if previous is not None and previous.pending and previous != recoverable:
                    raise ValueError("The previous sandbox replacement has not completed")
                await _records.patch(
                    key,
                    PendingReplacement(**pin.model_dump(), run_id=run_id).model_dump(mode="json"),
                    session=session,
                )
                publish = True
        if publish:
            await THREADS.update(thread_id=thread_id, metadata=metadata)
        return pin

    async def bind(
        self,
        thread_id: str,
        sandbox_id: str,
        profile: ExecutionProfile,
        metadata: JsonObject,
        *,
        replacement: ReplacementPin | None = None,
    ) -> None:
        from openswe.threads.store import THREADS

        execution = await profile.keep()
        key = f"replacement:{thread_id}"
        async with postgres.session() as session:
            data = await _records.lock(key, session=session)
            pending = PendingReplacement.model_validate(data) if data else None
            if (pending is not None and pending.pending) or replacement is not None:
                if pending is None or pending.pin() != replacement:
                    raise ValueError("The sandbox replacement changed; start a new run")
                if execution != pending.execution:
                    raise ValueError("The sandbox replacement authority does not match")
            sandbox_key = f"sandbox:{thread_id}:{sandbox_id}"
            bound = await _records.get(sandbox_key, session=session)
            if replacement is not None and pending is not None and not pending.pending:
                if not bound or bound.get("execution") != execution:
                    raise ValueError("The completed sandbox replacement has different authority")
                return
            if bound and bound.get("execution") != execution and replacement is None:
                raise ValueError("The sandbox already has different creation authority")
            await _records.patch(
                sandbox_key,
                SandboxAuthority(execution=execution).model_dump(mode="json"),
                session=session,
            )
        await THREADS.update(thread_id=thread_id, metadata=metadata)
        if replacement is not None:
            async with postgres.session() as session:
                data = await _records.lock(key, session=session)
                completed = PendingReplacement.model_validate(data)
                if completed.pin() != replacement:
                    raise ValueError("The sandbox replacement changed; start a new run")
                await _records.patch(key, {"pending": False}, session=session)


SANDBOX_EXECUTION = SandboxExecutionBindings()
