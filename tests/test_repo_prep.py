from __future__ import annotations

from deepagents.backends.protocol import ExecuteResponse

from agent.utils.repo_prep import discover_skill_sources, prepare_review_repo


class _FakeSandboxBackend:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        raise_exc: bool = False,
        output: str = "",
    ) -> None:
        self._exit_code = exit_code
        self._raise = raise_exc
        self._output = output
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "fake-sandbox"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        if self._raise:
            raise RuntimeError("sandbox unreachable")
        self.commands.append(command)
        return ExecuteResponse(output=self._output, exit_code=self._exit_code, truncated=False)


async def test_prepare_review_repo_clones_and_checks_out_head() -> None:
    backend = _FakeSandboxBackend()
    ok = await prepare_review_repo(
        backend,
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc123",
    )
    assert ok is True
    assert len(backend.commands) == 1
    cmd = backend.commands[0]
    assert "gh repo clone acme/widget" in cmd
    assert "/work/widget/.git" in cmd
    assert "git checkout abc123" in cmd


async def test_prepare_review_repo_skips_checkout_without_head() -> None:
    backend = _FakeSandboxBackend()
    ok = await prepare_review_repo(
        backend,
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="",
    )
    assert ok is True
    assert "git checkout" not in backend.commands[0]


async def test_prepare_review_repo_requires_owner_and_name() -> None:
    backend = _FakeSandboxBackend()
    ok = await prepare_review_repo(
        backend, work_dir="/work", repo_owner="", repo_name="widget", head_sha="abc"
    )
    assert ok is False
    assert backend.commands == []


async def test_prepare_review_repo_returns_false_on_nonzero_exit() -> None:
    backend = _FakeSandboxBackend(exit_code=1)
    ok = await prepare_review_repo(
        backend, work_dir="/work", repo_owner="acme", repo_name="widget", head_sha="abc"
    )
    assert ok is False


async def test_prepare_review_repo_returns_false_on_exception() -> None:
    backend = _FakeSandboxBackend(raise_exc=True)
    ok = await prepare_review_repo(
        backend, work_dir="/work", repo_owner="acme", repo_name="widget", head_sha="abc"
    )
    assert ok is False


async def test_discover_skill_sources_returns_only_existing_dirs() -> None:
    backend = _FakeSandboxBackend(output="/work/widget/.agents/skills\n")
    sources = await discover_skill_sources(backend, repo_dir="/work/widget")
    assert sources == ["/work/widget/.agents/skills/"]


async def test_discover_skill_sources_empty_when_none_exist() -> None:
    backend = _FakeSandboxBackend(output="")
    sources = await discover_skill_sources(backend, repo_dir="/work/widget")
    assert sources == []


async def test_discover_skill_sources_handles_exception() -> None:
    backend = _FakeSandboxBackend(raise_exc=True)
    sources = await discover_skill_sources(backend, repo_dir="/work/widget")
    assert sources == []
