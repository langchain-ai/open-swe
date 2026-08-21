from typing import cast

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from support.sandbox_fakes import FakeSandboxBackend

from agent.utils.repo_prep import materialize_trusted_skills, prepare_review_repo


def _quiet(exit_code: int = 0) -> FakeSandboxBackend:
    return FakeSandboxBackend(default=ExecuteResponse(output="", exit_code=exit_code))


def _unreachable() -> FakeSandboxBackend:
    return FakeSandboxBackend(default=RuntimeError("sandbox unreachable"))


async def test_prepare_review_repo_clones_and_checks_out_head() -> None:
    backend = _quiet()
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc123",
        pr_number=42,
        base_sha="def456",
    )
    assert ok is True
    assert len(backend.commands) == 1
    cmd = backend.commands[0]
    assert "gh repo clone acme/widget" in cmd
    assert "/work/widget/.git" in cmd
    assert "git fetch origin def456" in cmd
    assert "git fetch origin refs/pull/42/head" in cmd
    assert "git checkout --force abc123 --quiet" in cmd
    assert "git checkout --force abc123 --quiet 2>/dev/null || true" not in cmd
    assert '[ "$(git rev-parse HEAD)" = abc123 ]' in cmd
    assert "git fetch --all --quiet || true" in cmd


async def test_prepare_review_repo_skips_pull_ref_without_pr_number() -> None:
    backend = _quiet()
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc123",
    )
    assert ok is True
    assert "refs/pull" not in backend.commands[0]


async def test_prepare_review_repo_skips_checkout_without_head() -> None:
    backend = _quiet()
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="",
    )
    assert ok is True
    assert "git checkout" not in backend.commands[0]


async def test_prepare_review_repo_requires_owner_and_name() -> None:
    backend = _quiet()
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="",
        repo_name="widget",
        head_sha="abc",
    )
    assert ok is False
    assert backend.commands == []


async def test_prepare_review_repo_returns_false_on_nonzero_exit() -> None:
    backend = _quiet(exit_code=1)
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc",
    )
    assert ok is False


async def test_prepare_review_repo_returns_false_on_exception() -> None:
    backend = _unreachable()
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc",
    )
    assert ok is False


async def test_materialize_trusted_skills_extracts_from_trusted_ref() -> None:
    backend = FakeSandboxBackend(script=["/work/.review-skills/.agents/skills\n", ""])
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref="def456"
    )
    assert sources == ["/work/.review-skills/.agents/skills/"]
    assert len(backend.commands) == 2
    for cmd in backend.commands:
        assert "git cat-file -e def456:" in cmd
        assert "git archive def456" in cmd


async def test_materialize_trusted_skills_empty_without_ref() -> None:
    backend = _quiet()
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref=""
    )
    assert sources == []
    assert backend.commands == []


async def test_materialize_trusted_skills_empty_when_none_exist() -> None:
    backend = _quiet()
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref="def456"
    )
    assert sources == []


async def test_materialize_trusted_skills_handles_exception() -> None:
    backend = _unreachable()
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref="def456"
    )
    assert sources == []
