"""The repositories and branches a turn's work touched, for its trace's git metadata.

The checkouts under the run's work dir are fingerprinted in the thread's sandbox
(``sandbox_scripts/git_trace.py``): once as the turn starts, and again before a
model call that follows a file or shell tool, each in the background. A tool that
may change a checkout first waits, briefly, for the starting fingerprint, so that
never sees the tool's own change. Pull requests the run's operations open or find
name the branch a repository's work went to.

A fingerprint that fails, or skips every checkout, is retried no sooner than 30s
later, then 60s; after three in a row the turn takes no more. A change no fingerprint
could see, or one a tool made before the starting fingerprint was taken, may be
missing from the targets, so the trace says ``open_swe_git_incomplete``.

Once the run has ended, the targets are reconciled in the order the work happened
(``TurnTrace.end``): the first is the trace's ``ls_git_repo`` and ``ls_git_branch``,
a pull request's head branch preferred for its repository. ``open_swe_git_commits``
retains each observed checkout HEAD under its actual branch, before that preference.
Nothing waits for a fingerprint but a tool that may change a checkout, and only for the first.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import ClassVar, Final, Self

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import BaseModel, ConfigDict, JsonValue
from runtime_api.operations import PullRequestHead

from rt.backend import Run, RunLocal
from rt.sandbox_script import SandboxScript
from rt.sandbox_setup import SandboxUnavailableError

logger = logging.getLogger(__name__)

# The file and shell tools that may change a checkout.
OBSERVED_TOOLS: Final = frozenset({"execute", "write_file", "edit_file", "delete"})
_SCRIPT: Final = SandboxScript("git_trace.py")


class GitTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo: str
    branch: str
    head: str = ""


class GitCheckout(GitTarget):
    dirty: str = ""


class GitSnapshot(BaseModel):
    repos: dict[str, GitCheckout] = {}
    # Checkouts the script could not inspect, by path, with why.
    skipped: dict[str, str] = {}

    @property
    def failed(self) -> bool:
        """Whether the script skipped every checkout it found."""
        return bool(self.skipped) and not self.repos

    def changes_since(self, before: Self) -> list[GitTarget]:
        # A checkout skipped before is unknown then, so finding it now is no change.
        return [
            GitTarget(repo=checkout.repo, branch=checkout.branch, head=checkout.head)
            for path, checkout in self.repos.items()
            if path not in before.skipped and checkout != before.repos.get(path)
        ]


@dataclass(frozen=True)
class _Observed:
    at: int
    targets: tuple[GitTarget, ...]
    pull_requests: bool = False


class TurnGit:
    """One turn's git evidence, gathered off the path of the run's calls; each run has its
    own (``turn_git``)."""

    # How long a tool that may change a checkout waits for the starting fingerprint; a
    # cold sandbox's first scan has taken 25s.
    BASELINE_WAIT: ClassVar[float] = 5.0
    # How long to leave the checkouts alone after the first and later failed fingerprints.
    RETRY_AFTER: ClassVar[tuple[float, float]] = (30.0, 60.0)
    # Failed fingerprints in a row after which the turn takes no more.
    STOP_AFTER: ClassVar[int] = 3

    def __init__(self) -> None:
        self._sandbox: SandboxBackendProtocol | None = None
        self._root = ""
        self._baseline: asyncio.Task[GitSnapshot | None] | None = None
        self._fingerprinting: asyncio.Task[None] | None = None
        # When the first change no fingerprint has seen yet was made.
        self._changed_at: int | None = None
        self._observed: list[_Observed] = []
        self._failures = 0
        # The event loop's time before which no fingerprint is retried.
        self._retry_at = 0.0
        # Whether a tool that may change a checkout ran before the starting fingerprint.
        self._late = False

    def start(
        self,
        files: object,
        root: object,
        ready: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Fingerprint the checkouts under ``root`` in the thread's ``files`` as the turn
        starts, in the background, once ``ready`` says the sandbox has booted and has its
        work dir."""
        self._observed = []
        self._changed_at = None
        self._failures, self._retry_at, self._late = 0, 0.0, False
        if not isinstance(files, SandboxBackendProtocol) or not isinstance(root, str) or not root:
            self._baseline = None
            return
        self._sandbox, self._root = files, root
        self._baseline = asyncio.ensure_future(self._first_fingerprint(ready))

    async def changing(self) -> None:
        """Wait for the turn's starting fingerprint, before a tool that may change a checkout,
        for at most ``BASELINE_WAIT``: past that, the tool goes ahead and its change may be
        in the starting fingerprint."""
        if self._baseline is None or self._baseline.done() or self._late:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._baseline), self.BASELINE_WAIT)
        except TimeoutError:
            self._late = True
            logger.info("A tool that may change a checkout ran before the turn's git baseline")

    def changed(self, at: int) -> None:
        """A tool that may have changed a checkout ran, starting at ``at``."""
        if self._baseline is not None:
            self._pending(at)

    def settle(self) -> None:
        """Fingerprint, in the background, what tools changed since the last fingerprint,
        unless the last ones failed."""
        if asyncio.get_running_loop().time() >= self._retry_at:
            self._probe()

    def found_pull_requests(self, heads: Sequence[PullRequestHead], at: int) -> None:
        if heads:
            targets = tuple(GitTarget(repo=head.repo, branch=head.branch) for head in heads)
            self._observed.append(_Observed(at, targets, pull_requests=True))

    async def metadata(self) -> dict[str, JsonValue]:
        """What the turn's trace records of its git work: every target, earliest first."""
        if self._fingerprinting is not None:
            await asyncio.shield(self._fingerprinting)
        # The turn's last changes get one fingerprint, however recent the last failure.
        self._probe()
        if self._fingerprinting is not None:
            await asyncio.shield(self._fingerprinting)
        incomplete: dict[str, JsonValue] = (
            {"open_swe_git_incomplete": True} if self._late or self._changed_at is not None else {}
        )
        observed: list[GitTarget] = []
        pull_requests: dict[str, list[str]] = {}
        for observation in sorted(self._observed, key=lambda observed: observed.at):
            observed.extend(observation.targets)
            if observation.pull_requests:
                for target in observation.targets:
                    pull_requests.setdefault(target.repo, []).append(target.branch)
        targets: dict[tuple[str, str], None] = {}
        for target in observed:
            branches = pull_requests.get(target.repo, [])
            branch = target.branch if not branches or target.branch in branches else branches[0]
            targets.setdefault((target.repo, branch), None)
        if not targets:
            return incomplete
        repo, branch = next(iter(targets))
        commits: list[JsonValue] = [
            {"repo": target.repo, "branch": target.branch, "commit": target.head}
            for target in dict.fromkeys(observed)
            if target.head
        ]
        return {
            "open_swe_git_targets": [{"repo": repo, "branch": branch} for repo, branch in targets],
            **({"open_swe_git_commits": commits} if commits else {}),
            "ls_git_repo": repo,
            **({"ls_git_branch": branch} if branch else {}),
            **incomplete,
        }

    @property
    def _busy(self) -> bool:
        return self._fingerprinting is not None and not self._fingerprinting.done()

    @property
    def _stopped(self) -> bool:
        """Whether no fingerprint taken now could show a change: too many failed in a row,
        or there is no starting fingerprint to compare it with."""
        baseline = self._baseline
        if self._failures >= self.STOP_AFTER:
            return True
        if baseline is None or not baseline.done():
            return False
        return baseline.cancelled() or baseline.exception() is not None or baseline.result() is None

    def _pending(self, at: int) -> None:
        if self._changed_at is None or at < self._changed_at:
            self._changed_at = at

    def _probe(self) -> None:
        if self._changed_at is None or self._busy or self._stopped:
            return
        at, self._changed_at = self._changed_at, None
        self._fingerprinting = asyncio.ensure_future(self._observe(at))

    async def _observe(self, at: int) -> None:
        before = await asyncio.shield(self._baseline) if self._baseline is not None else None
        after = await self._fingerprint() if before is not None else None
        if before is None or after is None or after.failed:
            # The next fingerprint covers this change too, at its own time.
            self._pending(at)
            return
        self._observed.append(_Observed(at, tuple(after.changes_since(before))))

    async def _first_fingerprint(
        self, ready: Callable[[], Awaitable[None]] | None
    ) -> GitSnapshot | None:
        if ready is not None:
            try:
                await ready()
            except SandboxUnavailableError:
                # Reaching for a sandbox that never booted would open another; the run's
                # first call that needs the sandbox reports it.
                logger.warning("No git evidence: the thread's sandbox never booted")
                return None
        return await self._fingerprint()

    async def _fingerprint(self) -> GitSnapshot | None:
        if self._sandbox is None:
            return None
        try:
            snapshot = GitSnapshot.model_validate(
                await _SCRIPT.run(self._sandbox, {"root": self._root})
            )
        except Exception:
            logger.warning("Could not fingerprint the turn's checkouts", exc_info=True)
            self._failed()
            return None
        if snapshot.skipped:
            # The reasons go in the message too: deployment logs drop ``extra``.
            logger.warning(
                "Skipped checkouts in turn git evidence: %s",
                "; ".join(f"{path} ({reason})" for path, reason in snapshot.skipped.items()),
                extra={"skipped": snapshot.skipped},
            )
        if snapshot.failed:
            self._failed()
        else:
            self._failures = 0
        return snapshot

    def _failed(self) -> None:
        """Leave the checkouts alone for a while after a failed fingerprint, and for the
        rest of the turn after ``STOP_AFTER`` in a row."""
        self._failures += 1
        if self._failures >= self.STOP_AFTER:
            logger.warning(
                "Stopped fingerprinting the turn's checkouts after %d failures in a row",
                self._failures,
            )
            return
        delay = self.RETRY_AFTER[min(self._failures, len(self.RETRY_AFTER)) - 1]
        self._retry_at = asyncio.get_running_loop().time() + delay


_TURNS: Final = RunLocal(TurnGit)


def turn_git(run: Run) -> TurnGit:
    """The git evidence of the turn ``run`` is playing."""
    return _TURNS.get(run)
