"""The sandbox of a run's thread, as MDA keeps it, and what the backend does with it."""

import asyncio
import logging

from langchain_core.runnables import RunnableConfig
from runtime_api.catalog import SandboxSpec
from runtime_api.sandbox_setup import GitIdentity

from openswe.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from openswe.run_config import RunConfig
from openswe.sandboxes import lifecycle
from openswe.sandboxes.lifecycle import bind_mda_sandbox
from openswe.sandboxes.managed import ManagedSandbox
from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox
from openswe.sandboxes.state import (
    SANDBOX_BACKENDS,
    SandboxDeletedError,
    SandboxUnreachableError,
)
from openswe.tasks.schemas import ThreadMetadata

logger = logging.getLogger(__name__)


class RunSandbox:
    """The sandbox of the thread the run ``config`` names.

    A run that ``reviews`` a pull request reaches only its repository, and its own notice
    tells the pull request when the sandbox is gone.
    """

    def __init__(self, config: RunnableConfig, *, reviews: bool) -> None:
        self.config = config
        self.reviews = reviews
        self._cfg = RunConfig.from_config(config)

    def _repositories(self) -> list[str] | None:
        """The repositories a review reaches, its pull request's, which a task worker's
        coordinator's sandbox must not exceed; None for a run with its thread's whole scope."""
        if not self.reviews:
            return None
        return [self._cfg.repo.full_name] if self._cfg.repo else []

    async def managed(self) -> ManagedSandbox:
        """The sandbox as MDA creates it."""
        if self._cfg.thread_id is None:
            raise ValueError("This run has no thread")
        return await ManagedSandbox.for_thread(
            self._cfg.thread_id,
            github_repositories=self._repositories(),
            # A review's preparation works in the sandbox: its checkout and its diff.
            prepare_reaches_sandbox=self.reviews,
        )

    async def spec(self) -> SandboxSpec:
        """The sandbox MDA creates for the run, with who commits there.

        Who commits is read alongside the sandbox, not after it: the two read the thread
        and its owner independently, each in transactions of its own. An MDA deployment from
        before ``SandboxSpec.proxy_config`` reads ``writes_proxy``.
        """
        git_identity = asyncio.ensure_future(self.git_identity())
        try:
            sandbox = await self.managed()
        except BaseException as exc:
            await self._drop(git_identity)
            if isinstance(exc, SandboxUnreachableError):
                # The run can't be given a sandbox; it ends once the person knows why.
                await self.tell_gone(
                    sandbox_id=exc.sandbox_id,
                    reason=exc.reason,
                    deleted=isinstance(exc, SandboxDeletedError),
                )
            raise
        dumped = sandbox.model_dump(mode="json")
        return SandboxSpec.model_validate(
            {
                **dumped,
                # Not in the create options a run's execution profile records, whose digest
                # pins their shape.
                "create": {**dumped["create"], "env_vars": sandbox.env_vars},
                "git_identity": await git_identity,
                "writes_proxy": not sandbox.external,
            }
        )

    @staticmethod
    async def _drop(git_identity: asyncio.Future[GitIdentity]) -> None:
        """Stop a committer read the run no longer needs, and wait for it to end.

        Its result goes unused, so a read that already failed fails nothing more.
        """
        git_identity.cancel()
        await asyncio.wait([git_identity])
        if not git_identity.cancelled() and (error := git_identity.exception()) is not None:
            logger.debug("An unneeded sandbox committer read failed", exc_info=error)

    async def git_identity(self) -> GitIdentity:
        """Who commits in the sandbox: the thread's owner, or the bot when it has no GitHub one."""
        if self._cfg.thread_id is None:
            return GitIdentity()
        metadata = await lifecycle.get_sandbox_metadata(self._cfg.thread_id)
        return await ThreadMetadata.model_validate(metadata).git_identity()

    async def bind(self, sandbox_id: str | None, *, open_error: str | None) -> None:
        """Bind the thread to the sandbox MDA opened, ``sandbox_id``, or to none when it could
        not (``open_error`` says why); raises ``SandboxUnreachableError`` for a thread left
        with no sandbox to work in."""
        if self._cfg.thread_id is None:
            raise ValueError("This run has no thread")
        await bind_mda_sandbox(
            self._cfg.thread_id,
            sandbox_id,
            open_error=open_error,
        )

    async def bind_or_tell(self, sandbox_id: str | None, *, open_error: str | None) -> None:
        """Bind the thread to the sandbox MDA opened before preparing.

        A thread with no sandbox to work in ends the run once the person knows why.
        """
        try:
            await self.bind(sandbox_id, open_error=open_error)
        except SandboxUnreachableError as exc:
            await self.tell_gone(
                sandbox_id=exc.sandbox_id,
                reason=exc.reason,
                deleted=isinstance(exc, SandboxDeletedError),
            )
            raise

    async def finish_git_identity(self) -> None:
        """Write the bot's git identity a sandbox preparation connected to still has pending."""
        sandbox = SANDBOX_BACKENDS.get(self._cfg.thread_id or "")
        if sandbox is not None and sandbox.has_backend:
            backend = sandbox.current
            if isinstance(backend, TimeoutLangSmithSandbox):
                await backend.ensure_git_identity()

    async def tell_gone(
        self,
        *,
        sandbox_id: str | None = None,
        reason: str | None = None,
        deleted: bool | None = None,
    ) -> None:
        """Tell the person the sandbox is gone, unless a review's own notice tells its PR.

        One LangSmith ``deleted`` is told as such, since only a new thread goes on from it;
        unless the caller knows, LangSmith is asked about the sandbox the thread is bound to.
        """
        if self.reviews:
            return
        if deleted is None and self._cfg.thread_id is not None:
            metadata = await lifecycle.get_sandbox_metadata(self._cfg.thread_id)
            deleted = await ThreadMetadata.model_validate(metadata).mda_sandbox_deleted()
        await post_sandbox_unreachable_notification(
            self.config, sandbox_id=sandbox_id, reason=reason, deleted=bool(deleted)
        )
