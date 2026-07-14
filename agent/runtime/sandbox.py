from collections.abc import Awaitable, Callable, Sequence

from deepagents.backends.protocol import SandboxBackendProtocol


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    from agent.server import ensure_sandbox_for_thread as ensure

    return await ensure(
        thread_id,
        github_proxy_token=github_proxy_token,
        github_proxy_repositories=github_proxy_repositories,
        repo=repo,
    )


def get_cached_sandbox_backend(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProtocol:
    from agent.server import get_cached_sandbox_backend as get_backend

    return get_backend(thread_id, reconnect=reconnect)


async def configure_git_identity(sandbox_backend: SandboxBackendProtocol) -> None:
    from agent.server import configure_git_identity as configure

    await configure(sandbox_backend)


async def recreate_sandbox(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    from agent.server import recreate_sandbox as recreate

    return await recreate(
        thread_id,
        github_proxy_token=github_proxy_token,
        github_proxy_repositories=github_proxy_repositories,
        repo=repo,
    )
