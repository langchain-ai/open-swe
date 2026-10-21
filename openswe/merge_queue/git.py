"""Bare Git object operations in a sandbox; candidate trees are never checked out."""

import re
import shlex
from pathlib import PurePath
from typing import TYPE_CHECKING, Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
_BARE_CONFIG = "[core]\n\trepositoryformatversion = 0\n\tbare = true\n"
_PROXY_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "GIT_SSL_CAPATH",
)


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
    def __init__(self, number: int, reason: str):
        super().__init__(reason)
        self.number = number


class CandidateGit:
    def __init__(self, sandbox: SandboxBackendProtocol, directory: PurePath, remote: str):
        self.sandbox = sandbox
        self.directory = directory
        self.remote = remote

    async def initialize(self) -> None:
        await self._run(["init", "--bare", "--template="], initializing=True)

    async def _run(
        self, args: list[str], *, timestamp: int | None = None, initializing: bool = False
    ) -> str:
        directory, config = shlex.quote(str(self.directory)), shlex.quote(_BARE_CONFIG)
        setup = f"mkdir -m 700 -- {directory}\n" if initializing else ""
        setup += f"cd {directory}\n"
        if not initializing:
            setup += (
                "if [ -L config ] || [ -e commondir ] || [ -L commondir ] || "
                f"! printf %s {config} | cmp -s - config; then exit 97; fi\n"
            )
        env = {
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        if timestamp is not None:
            env.update(
                GIT_AUTHOR_DATE=f"{timestamp} +0000", GIT_COMMITTER_DATE=f"{timestamp} +0000"
            )
        # Keep the sandbox's proxy/TLS configuration, never its Git configuration or secrets.
        proxy_env = " ".join(f'${{{name}:+"{name}=${name}"}}' for name in _PROXY_ENV)
        command = shlex.join(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "core.hooksPath=/dev/null",
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
            ]
        )
        clean_env = shlex.join(["env", "-i", *(f"{key}={value}" for key, value in env.items())])
        command = f"set -e\n{setup}{clean_env} {proxy_env} {command} 2>/dev/null"
        if initializing:
            command += f"\nprintf %s {config} > config"
        result = await self.sandbox.aexecute(command, timeout=300)
        if result.exit_code == 97:
            raise ValueError("Unexpected candidate workspace configuration")
        if result.exit_code is None or result.truncated:
            raise RuntimeError("Candidate command result is incomplete")
        if result.exit_code:
            # Git output can contain remote-controlled text and credential diagnostics.
            raise GitError(result.exit_code, f"Git {args[0]} failed (exit {result.exit_code})")
        return result.output.strip()

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
                raise CandidateConflict(pull.number, f"PR #{pull.number} is already included")
            try:
                tree = await self._run(["merge-tree", "--write-tree", previous, pull.sha])
            except GitError as exc:
                if exc.code != 1:
                    raise
                raise CandidateConflict(
                    pull.number, f"Merge conflict in PR #{pull.number}"
                ) from exc
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
        sandbox: SandboxBackendProtocol,
        directory: PurePath,
        remote: str,
        *,
        canary_remote: str,
    ):
        if remote != canary_remote:
            raise ValueError("Landing is restricted to the configured canary")
        super().__init__(sandbox, directory, remote)

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
