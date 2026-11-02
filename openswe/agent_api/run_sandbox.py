"""The sandbox of a run's thread, as MDA keeps it, and what the backend does with it."""

from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime_api.loop import Prepare

from openswe.agent_api.runs import in_graph_context
from openswe.github.app import PermissionMap
from openswe.github.sandbox_access import READ_ONLY_REVIEW_PERMISSIONS
from openswe.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from openswe.run_config import RunConfig
from openswe.sandboxes.lifecycle import (
    bind_mda_sandbox,
    reconnect_replaced_sandbox,
    refresh_thread_github_proxy,
)
from openswe.sandboxes.managed import ManagedSandbox
from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox
from openswe.sandboxes.state import SANDBOX_BACKENDS, SandboxUnreachableError


class RunSandbox:
    """The sandbox of the thread the run ``config`` names.

    A run that ``reviews`` a pull request reaches only its repository, read-only, and its
    own notice tells the pull request when the sandbox is gone.
    """

    def __init__(self, config: RunnableConfig, *, reviews: bool) -> None:
        self.config = config
        self.reviews = reviews
        self._cfg = RunConfig.from_config(config)

    def _github_scope(self) -> tuple[list[str] | None, PermissionMap | None]:
        """How the run narrows its sandbox's GitHub token beyond its thread's own scope."""
        if not self.reviews:
            return None, None
        return [self._cfg.repo.full_name] if self._cfg.repo else [], READ_ONLY_REVIEW_PERMISSIONS

    async def managed(self) -> ManagedSandbox | None:
        """The sandbox as MDA creates it, when MDA owns it."""
        if self._cfg.thread_id is None:
            return None
        repositories, permissions = self._github_scope()
        return await ManagedSandbox.for_thread(
            self._cfg.thread_id,
            self._cfg.workspace_slug,
            allow_github=self._cfg.auth_provider != "langsmith",
            github_repositories=repositories,
            github_permissions=permissions,
        )

    async def bind(self, request: Prepare) -> None:
        """Bind the thread to the sandbox MDA opened before preparing.

        A thread with no sandbox to work in ends the run once the person knows why.
        """
        if self._cfg.thread_id is None:
            raise ValueError("This run has no thread")
        try:
            await bind_mda_sandbox(
                self._cfg.thread_id,
                request.sandbox_id,
                open_error=request.sandbox_error,
                workspace_slug=self._cfg.workspace_slug,
            )
        except SandboxUnreachableError as exc:
            await self.tell_gone(reason=exc.reason)
            raise

    async def finish_git_identity(self) -> None:
        """Write the bot's git identity a sandbox preparation connected to still has pending."""
        sandbox = SANDBOX_BACKENDS.get(self._cfg.thread_id or "")
        if sandbox is not None and sandbox.has_backend:
            backend = sandbox.current
            if isinstance(backend, TimeoutLangSmithSandbox):
                await backend.ensure_git_identity()

    async def reconnect(self) -> None:
        """Connect again to the sandbox, which MDA replaced with a fresh one."""

        async def reconnect(_runtime: Runtime) -> None:
            if self._cfg.thread_id is None:
                raise ValueError("This run has no thread")
            repositories, permissions = self._github_scope()
            await reconnect_replaced_sandbox(
                self._cfg.thread_id,
                workspace_slug=self._cfg.workspace_slug,
                github_proxy_repositories=repositories,
                github_proxy_permissions=permissions,
            )

        await in_graph_context({**self.config, "run_name": "sandbox"}, reconnect)

    async def refresh_github_proxy(self, *, narrowed: bool) -> datetime | None:
        """Give the sandbox's GitHub proxy a fresh token, narrowed as the run reaches GitHub
        when ``narrowed``; when the token expires, if the backend writes the proxy."""
        if self._cfg.thread_id is None:
            raise ValueError("This run has no thread")
        repositories, permissions = self._github_scope() if narrowed else (None, None)
        return await refresh_thread_github_proxy(
            self._cfg.thread_id,
            workspace_slug=self._cfg.workspace_slug,
            github_proxy_repositories=repositories,
            github_proxy_permissions=permissions,
        )

    async def tell_gone(self, *, sandbox_id: str | None = None, reason: str | None = None) -> None:
        """Tell the person the sandbox is gone, unless a review's own notice tells its PR."""
        if self.reviews:
            return
        await post_sandbox_unreachable_notification(
            self.config, sandbox_id=sandbox_id, reason=reason
        )
