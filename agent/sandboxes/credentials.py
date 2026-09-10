"""Open SWE's sandbox credentials: a GitHub App token on the LangSmith proxy."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agent.github.app import get_github_app_installation_token_with_expiry
from agent.github.proxy import get_recorded_proxy_base_config, record_proxy_token_expiry
from coding_agent.config import ENV
from coding_agent.sandboxes.credentials import InstalledCredentials, NoCredentials
from coding_agent.sandboxes.providers.langsmith import configure_github_proxy
from coding_agent.utils.startup_trace import aphase

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _InstalledProxyToken:
    base_config: dict[str, Any] | None
    expires_at: Any
    repositories: Sequence[str] | None

    async def bind(self, thread_id: str | None) -> None:
        record_proxy_token_expiry(
            thread_id,
            self.expires_at,
            repositories=self.repositories,
            permissions=None,
            base_proxy_config=self.base_config,
        )


@dataclass(frozen=True, slots=True)
class GitHubProxyCredentials:
    """The GitHub App installation token the sandbox's proxy injects.

    ``token`` and ``repositories`` narrow the credentials to one caller's scope;
    with neither, the proxy gets an installation-wide token.
    """

    token: str | None = None
    repositories: Sequence[str] | None = None

    async def install(
        self,
        sandbox_id: str,
        *,
        thread_id: str | None = None,
        base_proxy_config: dict[str, Any] | None = None,
    ) -> InstalledCredentials:
        if ENV.SANDBOX_TYPE.get() != "langsmith":
            return NoCredentials()

        async with aphase(thread_id, "sandbox.proxy_token"):
            token, expires_at = await self._resolve_token()
        if not token:
            msg = "Cannot configure proxy: GitHub App installation token is unavailable"
            logger.error(msg)
            raise ValueError(msg)

        kwargs: dict[str, Any] = {}
        if base_proxy_config is not None:
            kwargs["base_proxy_config"] = base_proxy_config
        async with aphase(thread_id, "sandbox.proxy_configure"):
            await configure_github_proxy(sandbox_id, token, **kwargs)

        return _InstalledProxyToken(
            base_config=dict(base_proxy_config) if base_proxy_config is not None else None,
            expires_at=expires_at,
            repositories=self.repositories,
        )

    async def recorded_base_config(self, thread_id: str | None) -> dict[str, Any] | None:
        return get_recorded_proxy_base_config(thread_id)

    async def _resolve_token(self) -> tuple[str | None, Any]:
        if self.token:
            return self.token, None
        return await get_github_app_installation_token_with_expiry()
