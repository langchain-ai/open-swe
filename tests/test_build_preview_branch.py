from pathlib import Path

import pytest

from scripts import build_preview_branch as builder
from scripts.build_preview_branch import (
    AGENT_NOTE,
    FIXUP_MESSAGE,
    INPUTS_REF,
    RERERE_NOTE,
    AgentReport,
    BuildInputs,
    Merged,
    Pending,
    Preview,
    PreviewError,
    Pull,
    RerereCache,
    Settings,
    git,
    rev_parse,
)


async def commit_file(path: str, content: str, message: str) -> str:
    Path(path).write_text(content)
    await git("add", path)
    await git("commit", "-qm", message)
    return await rev_parse("HEAD")


@pytest.fixture
async def preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Preview:
    prompt_dir = Path.cwd() / ".github/prompts"
    monkeypatch.setattr(builder, "PROMPT_PATH", prompt_dir / "resolve_preview_conflict.md")
    monkeypatch.setattr(builder, "FIX_PROMPT_PATH", prompt_dir / "fix_preview_typecheck.md")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.delenv("FORCE", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    remote = tmp_path / "remote.git"
    await git("init", "--bare", str(remote))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    await git("init", "-b", "main")
    await git("config", "user.name", "Test")
    await git("config", "user.email", "test@example.com")
    await git("remote", "add", "origin", str(remote))
    main = await commit_file("file.tsx", "base\n", "base")
    await git("push", "-u", "origin", "main")
    result = Preview(Settings.from_env())
    result.inputs = BuildInputs(main, (), None)
    return result


def pull(number: int, sha: str) -> Pull:
    return Pull.model_validate(
        {
            "number": number,
            "title": "change",
            "html_url": f"https://example.com/{number}",
            "head": {"sha": sha, "repo": {"full_name": "owner/repo"}},
            "user": {"login": "author"},
            "labels": [{"name": "preview"}],
        }
    )


async def test_reuses_published_fixup_without_discarding_rerere(
    preview: Preview, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = await rev_parse("HEAD")
    fixed = await commit_file("file.tsx", "fixed\n", FIXUP_MESSAGE)
    await git("push", "origin", "HEAD:refs/heads/preview")
    published = await preview.fetch_published()
    await git("reset", "--hard", before)
    cache = RerereCache(restored_tree="cached")
    builder.RERERE_DIR.mkdir()
    marker = builder.RERERE_DIR / "resolution"
    marker.write_text("keep")

    async def check() -> str | None:
        return None if Path("file.tsx").read_text() == "fixed\n" else "unrelated errors"

    monkeypatch.setattr(builder, "typecheck", check)
    assert await preview.verify(None, cache, published) is None
    assert await rev_parse("HEAD^{tree}") == await rev_parse(f"{fixed}^{{tree}}")
    assert cache.restored_tree == "cached"
    assert marker.read_text() == "keep"


@pytest.mark.parametrize("conflicted", [False, True])
async def test_remaining_errors_only_discard_cache_for_conflict_paths(
    preview: Preview, monkeypatch: pytest.MonkeyPatch, conflicted: bool
) -> None:
    cache = RerereCache(restored_tree="cached")
    builder.RERERE_DIR.mkdir()
    marker = builder.RERERE_DIR / "resolution"
    marker.write_text("keep")
    preview.conflict_paths.add("ui/src/file.tsx")
    path = "src/file.tsx" if conflicted else "src/unrelated.tsx"
    errors = f"{path}(1,2): error TS2322: broken"

    async def check() -> str | None:
        return None if Path("reassembled").exists() else errors

    async def fix(prompt: str, errors: str, timeout: float) -> None:
        await commit_file("fix-attempt", "attempt", FIXUP_MESSAGE)

    async def assemble(self: Preview, prompt: str | None) -> None:
        await commit_file("reassembled", "fresh resolution", "merge repair")

    monkeypatch.setattr(builder, "typecheck", check)
    monkeypatch.setattr(builder, "fix_with_agent", fix)
    monkeypatch.setattr(Preview, "assemble", assemble)
    result = await preview.verify("conflict prompt", cache)
    assert Path("fix-attempt").exists()
    if conflicted:
        assert result is None
        assert Path("reassembled").exists()
        assert cache.restored_tree is None
        assert not marker.exists()
    else:
        assert result == errors
        assert not Path("reassembled").exists()
        assert cache.restored_tree == "cached"
        assert marker.read_text() == "keep"


async def test_successful_agent_fix_retains_conflict_cache(
    preview: Preview, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = RerereCache(restored_tree="cached")
    preview.conflict_paths.add("file.tsx")

    async def check() -> str | None:
        return (
            None
            if Path("file.tsx").read_text() == "fixed"
            else "file.tsx(1,1): error TS2304: broken"
        )

    async def fix(prompt: str, errors: str, timeout: float) -> None:
        await commit_file("file.tsx", "fixed", FIXUP_MESSAGE)

    monkeypatch.setattr(builder, "typecheck", check)
    monkeypatch.setattr(builder, "fix_with_agent", fix)
    assert await preview.verify("conflict prompt", cache) is None
    assert cache.restored_tree == "cached"


async def test_rerere_replayed_paths_remain_available_for_verification(preview: Preview) -> None:
    await git("config", "rerere.enabled", "true")
    await git("config", "rerere.autoUpdate", "true")
    base = await rev_parse("HEAD")
    await git("checkout", "-b", "other")
    other = await commit_file("file.tsx", "other\n", "other")
    await git("checkout", "main")
    before = await commit_file("file.tsx", "main\n", "main change")
    await git("merge", "--no-ff", other, check=False)
    await commit_file("file.tsx", "resolved\n", "resolution")
    await git("reset", "--hard", before)
    outcome = await builder.merge(other, "merge other")
    assert outcome == Merged(RERERE_NOTE, ("file.tsx",))
    await git("reset", "--hard", before)
    preview.inputs = BuildInputs(base, (), other)
    await preview.merge_manual_branch()
    assert "file.tsx" in preview.conflict_paths
    assert preview.errors_touch_conflicts("file.tsx(2,3): error TS2304: broken")


@pytest.mark.parametrize("trailing_fixes", [False, True])
async def test_agent_trailing_fixes_consolidated_without_rewriting_merges(
    preview: Preview, monkeypatch: pytest.MonkeyPatch, trailing_fixes: bool
) -> None:
    await git("checkout", "-b", "pr")
    sha = await commit_file("file.tsx", "pr\n", "pr")
    await git("checkout", "main")
    before = await commit_file("file.tsx", "main\n", "pre-agent commit")
    merged = ""
    tree = ""

    async def resolve(prompt: str, pending: list[Pending], timeout: float) -> AgentReport:
        nonlocal merged, tree
        await git("merge", "--no-ff", sha, check=False)
        merged = await commit_file("file.tsx", "combined\n", "conflict merge")
        if trailing_fixes:
            await commit_file("unrelated.tsx", "type fix", "agent type fix")
            await commit_file("lint.tsx", "lint fix", "agent lint fix")
        tree = await rev_parse("HEAD^{tree}")
        return AgentReport(frozenset({1}), {})

    monkeypatch.setattr(builder, "resolve_with_agent", resolve)
    await preview.merge_pending("prompt", [Pending(pull(1, sha), sha, ("file.tsx",))])
    assert await rev_parse("HEAD^{tree}") == tree
    if trailing_fixes:
        assert await rev_parse("HEAD^") == merged
        assert (await git("log", "-1", "--format=%s")).stdout.strip() == FIXUP_MESSAGE
    else:
        assert await rev_parse("HEAD") == merged
    assert await rev_parse(f"{merged}^") == before
    assert AGENT_NOTE in preview.included[0]


@pytest.mark.parametrize("changed", [False, True])
async def test_publication_records_inputs_even_when_tree_unchanged(
    preview: Preview, changed: bool
) -> None:
    published = None if changed else await rev_parse("HEAD^{tree}")
    await preview.publish(published)
    await git("fetch", "origin", f"{INPUTS_REF}:{INPUTS_REF}")
    assert preview.inputs is not None
    assert (
        await git("log", "-1", "--format=%s", INPUTS_REF)
    ).stdout.strip() == preview.inputs.fingerprint


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "main",
        "pr",
        "label",
        "manual-created",
        "manual-moved",
        "manual-deleted",
        "force",
        "dispatch",
    ],
)
async def test_scheduled_build_skips_only_identical_nonforced_inputs(
    preview: Preview,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: str,
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    pulls = [pull(1, await rev_parse("HEAD"))] if change in {"pr", "label"} else []

    async def listing(repo: str) -> list[Pull]:
        return pulls

    async def check() -> None:
        return None

    async def assemble(self: Preview, prompt: str | None) -> None:
        await commit_file("assembled", "built", "assembled")

    monkeypatch.setattr(builder, "open_pulls", listing)
    monkeypatch.setattr(builder, "typecheck", check)
    monkeypatch.setattr(Preview, "assemble", assemble)
    if change in {"manual-moved", "manual-deleted"}:
        await git("push", "origin", "HEAD:refs/heads/preview-manual")
    preview.inputs = await BuildInputs.load(preview.settings)
    await preview.publish(None)
    if change == "main":
        await commit_file("main-change", "changed", "main change")
        await git("push", "origin", "main")
    elif change == "pr":
        pulls[0] = pull(1, await commit_file("pr-change", "changed", "pr change"))
    elif change == "label":
        pulls.clear()
    elif change in {"manual-created", "manual-moved"}:
        sha = await commit_file("manual-change", "changed", "manual change")
        await git("push", "origin", f"{sha}:refs/heads/preview-manual")
    elif change == "manual-deleted":
        await git("push", "origin", "--delete", "preview-manual")
    elif change == "force":
        monkeypatch.setenv("FORCE", "true")
        preview.settings = Settings.from_env()
    elif change == "dispatch":
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    await preview.build()
    if change == "none":
        assert not Path("assembled").exists()
        assert "inputs unchanged" in capsys.readouterr().out
    else:
        assert Path("assembled").read_text() == "built"


async def test_failed_verification_does_not_record_inputs(
    preview: Preview, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def listing(repo: str) -> list[Pull]:
        return []

    async def verify(
        self: Preview, prompt: str | None, cache: RerereCache, published: str | None
    ) -> str:
        return "still broken"

    monkeypatch.setattr(builder, "open_pulls", listing)
    monkeypatch.setattr(Preview, "verify", verify)
    with pytest.raises(PreviewError, match="nothing was published"):
        await preview.build()
    assert not await builder.remote_refs(INPUTS_REF)
