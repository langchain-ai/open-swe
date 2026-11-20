"""Immutable server-resolved execution profiles named by signed run tokens."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS
from openswe.dashboard.settings_records import SettingsRecords
from openswe.database import postgres
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions
from openswe.utils.json_types import JsonObject
from openswe.utils.time import parse_iso

_profile: ContextVar[ExecutionProfile | None] = ContextVar("execution_profile", default=None)
_records = SettingsRecords("execution")


class SandboxAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReplacementPin(SandboxAuthority):
    invocation_id: str = Field(min_length=1)


class ExecutionProfile(BaseModel):
    """Pinned run data whose historical serialization stays frozen for digest verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uses_defaults: bool = False
    create: LangSmithCreateOptions
    name: str | None = None
    instructions: str = ""
    repos: list[str] = Field(default_factory=list)
    refresh_workspace: str | None = None
    captured_at: str | None = None
    refresh_enabled: bool = False
    # Nothing sets it; profiles kept with one must still load and hash as they were kept.
    replacement: ReplacementPin | None = None

    @classmethod
    async def resolve(cls) -> Self:
        from openswe.execution_images import EXECUTION_IMAGES
        from openswe.sandboxes.lifecycle import SandboxCreateConfig

        defaults = await EXECUTION_DEFAULTS.load()
        image = await EXECUTION_IMAGES.state()
        return cls(
            uses_defaults=True,
            create=SandboxCreateConfig.from_defaults(defaults).langsmith_options(),
            instructions=defaults.prompt,
            repos=defaults.repos,
            captured_at=image.captured_at(defaults.snapshot_id),
            refresh_enabled=bool(defaults.update_script),
        )

    def stale(self, *, interval_seconds: int = 8 * 60 * 60) -> bool:
        if not self.refresh_enabled or not self.create.snapshot_id:
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
    @contextmanager
    def bind(cls, profile: ExecutionProfile | None) -> Iterator[None]:
        profile_token = _profile.set(profile)
        try:
            yield
        finally:
            _profile.reset(profile_token)

    def prompt(self) -> str:
        from openswe.prompt import SystemPrompt

        return SystemPrompt.execution_section(self.instructions, self.repos)


class SandboxExecutionBindings:
    """Private authority records retained across token expiry and thread deletion, without GC."""

    async def bound(self, thread_id: str, sandbox_id: str) -> ExecutionProfile | None:
        data = await _records.get(f"sandbox:{thread_id}:{sandbox_id}")
        if not data:
            return None
        pin = SandboxAuthority.model_validate(data)
        return await ExecutionProfile.kept(pin.execution)

    async def bind(
        self, thread_id: str, sandbox_id: str, profile: ExecutionProfile, metadata: JsonObject
    ) -> None:
        from openswe.threads.store import THREADS

        execution = await profile.keep()
        sandbox_key = f"sandbox:{thread_id}:{sandbox_id}"
        async with postgres.session() as session:
            bound = await _records.get(sandbox_key, session=session)
            if bound and bound.get("execution") != execution:
                raise ValueError("The sandbox already has different creation authority")
            await _records.patch(
                sandbox_key,
                SandboxAuthority(execution=execution).model_dump(mode="json"),
                session=session,
            )
        await THREADS.update(thread_id=thread_id, metadata=metadata)


SANDBOX_EXECUTION = SandboxExecutionBindings()
