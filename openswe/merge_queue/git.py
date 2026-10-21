"""Bare Git object operations. Candidate trees are never checked out or executed."""

import asyncio
import os
import re
import signal
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_BARE_CONFIG = b"[core]\n\trepositoryformatversion = 0\n\tbare = true\n"


class PullHead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    number: int = Field(gt=0)
    sha: SHA


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    repository: str
    branch: str
    base_sha: SHA
    pulls: tuple[PullHead, ...]
    merges: tuple[SHA, ...]
    tree_sha: SHA

    @model_validator(mode="after")
    def distinct_members(self) -> Self:
        if not self.pulls or len(self.pulls) != len(self.merges):
            raise ValueError("Expected one merge commit per PR")
        if len({p.number for p in self.pulls}) != len(self.pulls) or len(
            {p.sha for p in self.pulls}
        ) != len(self.pulls):
            raise ValueError("Duplicate PR or head")
        return self

    @property
    def sha(self) -> str:
        return self.merges[-1]


def candidate_ref(batch_id: UUID) -> str:
    return f"refs/merge-queue/candidates/{batch_id}"


class GitError(RuntimeError):
    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code = code


class CandidateConflict(ValueError):
    pass


class CandidateGit:
    def __init__(self, directory: Path, remote: str, *, authorization: SecretStr | None = None):
        if authorization is not None and not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+\.git", remote
        ):
            raise ValueError("Authenticated Git requires a GitHub repository URL")
        self.directory = directory
        self.remote = remote
        self._authorization = authorization

    async def initialize(self) -> None:
        self.directory.mkdir(mode=0o700)
        await self._run(["init", "--bare", "--template="], initializing=True)
        (self.directory / "config").write_bytes(_BARE_CONFIG)

    async def _run(
        self, args: list[str], *, timestamp: int | None = None, initializing: bool = False
    ) -> str:
        config = self.directory / "config"
        if not initializing and (
            config.is_symlink()
            or (self.directory / "commondir").exists()
            or config.read_bytes() != _BARE_CONFIG
        ):
            raise ValueError("Unexpected candidate workspace configuration")
        env = {
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        if timestamp is not None:
            env.update(
                GIT_AUTHOR_DATE=f"{timestamp} +0000", GIT_COMMITTER_DATE=f"{timestamp} +0000"
            )
        if self._authorization is not None:
            env.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0=f"http.{self.remote}.extraHeader",
                GIT_CONFIG_VALUE_0=f"Authorization: {self._authorization.get_secret_value()}",
            )
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "credential.helper=",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Merge Queue",
            "-c",
            "user.email=merge-queue@example.invalid",
            "-c",
            "http.followRedirects=false",
            *([] if initializing else ["--git-dir=."]),
            *args,
            cwd=self.directory,
            env=env,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(300):
                stdout, stderr = await process.communicate()
        except BaseException:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise
        if process.returncode:
            # Git output can contain remote-controlled text and credential diagnostics.
            raise GitError(process.returncode, f"Git {args[0]} failed (exit {process.returncode})")
        return stdout.decode().strip()

    async def build(
        self, branch: str, base_sha: str, pulls: tuple[PullHead, ...], *, timestamp: int
    ) -> Candidate:
        if not re.fullmatch(r"[0-9a-f]{40}", base_sha) or timestamp <= 0 or not pulls:
            raise ValueError("Invalid candidate inputs")
        if len({p.sha for p in pulls}) != len(pulls) or len({p.number for p in pulls}) != len(
            pulls
        ):
            raise ValueError("Duplicate PR or head")
        await self._run(["check-ref-format", f"refs/heads/{branch}"])
        for sha in (base_sha, *(p.sha for p in pulls)):
            await self._run(["fetch", "--no-tags", "--no-write-fetch-head", self.remote, sha])
            if await self._run(["cat-file", "-t", sha]) != "commit":
                raise ValueError("Batch inputs must be commits")
        previous = base_sha
        merges: list[str] = []
        for pull in pulls:
            if await self._run(["merge-base", previous, pull.sha]) == pull.sha:
                raise CandidateConflict(f"PR #{pull.number} is already included")
            try:
                tree = await self._run(["merge-tree", "--write-tree", previous, pull.sha])
            except GitError as exc:
                if exc.code != 1:
                    raise
                raise CandidateConflict(f"Merge conflict in PR #{pull.number}") from exc
            previous = await self._run(
                [
                    "commit-tree",
                    tree,
                    "-p",
                    previous,
                    "-p",
                    pull.sha,
                    "-m",
                    f"Merge pull request #{pull.number}\n\nMerge queue candidate.",
                ],
                timestamp=timestamp,
            )
            merges.append(previous)
        return Candidate(
            repository=self.remote,
            branch=branch,
            base_sha=base_sha,
            pulls=pulls,
            merges=tuple(merges),
            tree_sha=tree,
        )

    async def verify(self, candidate: Candidate) -> None:
        if candidate.repository != self.remote:
            raise ValueError("Candidate repository mismatch")
        await self._run(["check-ref-format", f"refs/heads/{candidate.branch}"])
        previous = candidate.base_sha
        for sha, pull in zip(candidate.merges, candidate.pulls, strict=True):
            if (await self._run(["rev-list", "--parents", "-n", "1", sha])).split() != [
                sha,
                previous,
                pull.sha,
            ]:
                raise ValueError("Candidate parents do not match membership")
            tree = await self._run(["merge-tree", "--write-tree", previous, pull.sha])
            if await self._run(["rev-parse", f"{sha}^{{tree}}"]) != tree:
                raise ValueError("Candidate tree does not match membership")
            previous = sha
        if await self._run(["rev-parse", f"{candidate.sha}^{{tree}}"]) != candidate.tree_sha:
            raise ValueError("Candidate tree does not match manifest")

    async def remote_sha(self, ref: str) -> str | None:
        await self._run(["check-ref-format", ref])
        for line in (await self._run(["ls-remote", "--refs", self.remote, ref])).splitlines():
            sha, name = line.split()
            if name == ref:
                return sha
        return None

    async def publish(self, candidate: Candidate, batch_id: UUID) -> None:
        await self.verify(candidate)
        ref = candidate_ref(batch_id)
        current = await self.remote_sha(ref)
        if current == candidate.sha:
            return
        if current is not None:
            raise ValueError("Candidate ref already exists with another SHA")
        try:
            await self._run(
                [
                    "push",
                    "--porcelain",
                    f"--force-with-lease={ref}:",
                    self.remote,
                    f"{candidate.sha}:{ref}",
                ]
            )
        except GitError:
            if await self.remote_sha(ref) != candidate.sha:
                raise
        if await self.remote_sha(ref) != candidate.sha:
            raise RuntimeError("Candidate publication is unconfirmed")

    async def delete(self, batch_id: UUID, sha: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid candidate SHA")
        ref = candidate_ref(batch_id)
        current = await self.remote_sha(ref)
        if current is None:
            return
        if current != sha:
            raise ValueError("Candidate ref changed; refusing deletion")
        try:
            await self._run(
                ["push", "--porcelain", f"--force-with-lease={ref}:{sha}", self.remote, f":{ref}"]
            )
        except GitError:
            if await self.remote_sha(ref) is not None:
                raise
        if await self.remote_sha(ref) is not None:
            raise RuntimeError("Candidate deletion is unconfirmed")


class CanaryLanding(CandidateGit):
    """Harness-only exact-tip proof. The scheduler never constructs this object."""

    def __init__(
        self,
        directory: Path,
        remote: str,
        *,
        canary_remote: str,
        authorization: SecretStr | None = None,
    ):
        if remote != canary_remote:
            raise ValueError("Landing is restricted to the configured canary")
        super().__init__(directory, remote, authorization=authorization)

    async def reconcile(self, candidate: Candidate) -> Literal["ready", "landed", "stale"]:
        await self.verify(candidate)
        sha = await self.remote_sha(f"refs/heads/{candidate.branch}")
        if sha == candidate.sha:
            return "landed"
        if sha == candidate.base_sha:
            return "ready"
        if sha is not None:
            await self._run(["fetch", "--no-tags", "--no-write-fetch-head", self.remote, sha])
            try:
                await self._run(["merge-base", "--is-ancestor", candidate.sha, sha])
            except GitError as exc:
                if exc.code != 1:
                    raise
            else:
                return "landed"
        return "stale"

    async def promote(self, candidate: Candidate) -> Literal["landed", "stale"]:
        before = await self.reconcile(candidate)
        if before != "ready":
            return before
        ref = f"refs/heads/{candidate.branch}"
        try:
            await self._run(
                [
                    "push",
                    "--porcelain",
                    f"--force-with-lease={ref}:{candidate.base_sha}",
                    self.remote,
                    f"{candidate.sha}:{ref}",
                ]
            )
        except GitError:
            after = await self.reconcile(candidate)
            if after == "ready":
                raise
            return after
        after = await self.reconcile(candidate)
        if after == "ready":
            raise RuntimeError("Landing outcome unknown; reconcile before retrying")
        return after
