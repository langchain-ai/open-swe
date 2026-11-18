"""Bounded recovery proposals; only the controller may bind their thread or execute them."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openswe.github.http import RepoClient
from openswe.github.queue import NativeEntry, NativeQueueSnapshot
from openswe.merge_queue.git import SHA


class RecoverySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: str = Field(pattern=r"^[CG][A-Z0-9]+$")
    operator_id: UUID


class RecoveryThread(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    settings: RecoverySettings
    thread_id: str
    thread_ts: str | None = None
    delivered: str | None = None
    notices: dict[str, str] = Field(default_factory=dict)
    followup_minutes: int = Field(default=15, ge=1, le=60)
    settled: bool = False


class RecoveryMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(min_length=1, max_length=256)
    number: int = Field(strict=True, gt=0)
    head_sha: SHA

    async def matches(self, entry: NativeEntry) -> bool:
        return (self.entry_id, self.number, self.head_sha) == (
            entry.id,
            entry.pull.number,
            entry.pull.head_sha,
        )


class RecoveryProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_key: UUID
    batch_id: UUID
    attempt_id: UUID
    candidate_sha: SHA
    action: Literal["retry", "defer", "pause", "escalate"]
    reason: str = Field(min_length=1, max_length=4000)
    suggested_fix: str = Field(default="", max_length=2000)
    member: RecoveryMember | None = None

    @model_validator(mode="after")
    def member_required_only_for_deferral(self) -> Self:
        if (self.action == "defer") != (self.member is not None):
            raise ValueError("Only deferral requires an exact queue member")
        return self


class RecoveryDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["native-recovery"] = "native-recovery"
    proposal: RecoveryProposal
    thread_id: str
    members: tuple[RecoveryMember, ...]
    generation: str | None
    status: Literal["pending", "applying", "applied", "escalated"]
    detail: str = ""

    async def check_dependencies(self, repo: RepoClient, snapshot: NativeQueueSnapshot) -> None:
        """Shared unmerged ancestry makes automatic removal unsafe, including later arrivals."""
        member = self.proposal.member
        assert member is not None
        if snapshot.queue is None:
            raise ValueError("Native queue disappeared")
        ancestors = {snapshot.main_sha}
        for entry in snapshot.queue.entries.nodes:
            if await member.matches(entry):
                continue
            common = await repo.merge_base(member.head_sha, entry.pull.head_sha)
            if common not in ancestors:
                if await repo.merge_base(common, snapshot.main_sha) != common:
                    raise ValueError(
                        "PRs share unmerged commits; operator must resolve dependencies"
                    )
                ancestors.add(common)
