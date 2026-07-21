from __future__ import annotations

from typing import cast

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from agent.utils.repo_prep import (
    materialize_trusted_skills,
    prepare_main_agent_repo_skills,
    prepare_review_repo,
)


class _FakeSandboxBackend:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        raise_exc: bool = False,
        output: str = "",
        outputs: list[str] | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._raise = raise_exc
        self._output = output
        self._outputs = outputs
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "fake-sandbox"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        if self._raise:
            raise RuntimeError("sandbox unreachable")
        self.commands.append(command)
        output = self._output
        if self._outputs is not None:
            output = self._outputs[len(self.commands) - 1]
        return ExecuteResponse(output=output, exit_code=self._exit_code, truncated=False)


async def test_prepare_review_repo_clones_and_checks_out_head() -> None:
    backend = _FakeSandboxBackend()
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
    backend = _FakeSandboxBackend()
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
    backend = _FakeSandboxBackend()
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
    backend = _FakeSandboxBackend()
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
    backend = _FakeSandboxBackend(exit_code=1)
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc",
    )
    assert ok is False


async def test_prepare_review_repo_returns_false_on_exception() -> None:
    backend = _FakeSandboxBackend(raise_exc=True)
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc",
    )
    assert ok is False


async def test_materialize_trusted_skills_extracts_from_trusted_ref() -> None:
    backend = _FakeSandboxBackend(
        outputs=[
            "OPEN_SWE_SKILLS_CACHE=miss\n"
            "OPEN_SWE_SKILLS_SOURCE=/work/.review-skills/.agents/skills\n",
            "",
        ]
    )
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref="def456"
    )
    assert sources == ["/work/.review-skills/.agents/skills/"]
    assert len(backend.commands) == 2
    for cmd in backend.commands:
        assert "git cat-file -e def456:" in cmd
        assert "git archive def456" in cmd


async def test_materialize_trusted_skills_empty_without_ref() -> None:
    backend = _FakeSandboxBackend()
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref=""
    )
    assert sources == []
    assert backend.commands == []


async def test_materialize_trusted_skills_empty_when_none_exist() -> None:
    backend = _FakeSandboxBackend(output="")
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref="def456"
    )
    assert sources == []


async def test_materialize_trusted_skills_handles_exception() -> None:
    backend = _FakeSandboxBackend(raise_exc=True)
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref="def456"
    )
    assert sources == []


async def test_prepare_main_agent_repo_skills_uses_base_sha() -> None:
    trusted_ref = "a" * 40
    backend = _FakeSandboxBackend(
        outputs=[
            f"OPEN_SWE_TRUSTED_REF={trusted_ref}\n",
            "OPEN_SWE_SKILLS_CACHE=miss\n"
            "OPEN_SWE_SKILLS_SOURCE=/work/.agent-skills/acme/widget/.agents/skills\n",
        ]
    )

    prepared = await prepare_main_agent_repo_skills(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        base_sha=trusted_ref,
    )

    assert prepared.trusted_ref == trusted_ref
    assert prepared.sources == ("/work/.agent-skills/acme/widget/.agents/skills/",)
    prep_command, extraction_command = backend.commands
    assert "git remote set-url origin https://github.com/acme/widget.git" in prep_command
    assert f"git fetch origin {trusted_ref}" in prep_command
    assert "git fetch origin HEAD" not in prep_command
    assert f"git archive {trusted_ref} .agents/skills" in extraction_command
    assert "chmod -R a-w /work/.agent-skills/acme/widget/.agents/skills" in extraction_command
    assert "/work/widget/.agents/skills" not in prepared.sources[0]


async def test_prepare_main_agent_repo_skills_resolves_default_branch_commit() -> None:
    trusted_ref = "b" * 40
    backend = _FakeSandboxBackend(outputs=[f"OPEN_SWE_TRUSTED_REF={trusted_ref}\n", ""])

    prepared = await prepare_main_agent_repo_skills(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
    )

    assert prepared.trusted_ref == trusted_ref
    assert prepared.sources == ()
    assert "git fetch origin HEAD --quiet" in backend.commands[0]
    assert "FETCH_HEAD^{commit}" in backend.commands[0]


async def test_prepare_main_agent_repo_skills_rejects_unsafe_repo_identifier() -> None:
    backend = _FakeSandboxBackend()

    prepared = await prepare_main_agent_repo_skills(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="../widget",
    )

    assert prepared.sources == ()
    assert backend.commands == []
