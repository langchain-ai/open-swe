import subprocess
from pathlib import Path

from openswe.middleware.design_snapshot import snapshot


def test_snapshot_observes_content_edits_without_dirty_false_positives(tmp_path: Path):
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    skill = repo / ".agents/skills/design-system/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Use repository components.")
    frontend = repo / "src/App.tsx"
    frontend.parent.mkdir()
    frontend.write_text("already dirty")
    initial = snapshot(tmp_path)
    assert str(frontend) in initial
    assert snapshot(tmp_path) == initial
    frontend.write_text("edited")
    assert snapshot(tmp_path)[str(frontend)] != initial[str(frontend)]


def test_snapshot_requires_design_guidance_and_ignores_gitignored_files(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "App.tsx").write_text("ui")
    assert snapshot(tmp_path) == {}
    skill = tmp_path / ".claude/skills/macaw/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Macaw")
    (tmp_path / ".gitignore").write_text("ignored.tsx\n")
    (tmp_path / "ignored.tsx").write_text("ignored")
    assert str(tmp_path / "App.tsx") in snapshot(tmp_path)
    assert str(tmp_path / "ignored.tsx") not in snapshot(tmp_path)


def test_snapshot_reuses_hashes_and_isolates_repository_failures(tmp_path: Path):
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    skill = repo / ".agents/skills/macaw/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Macaw")
    frontend = repo / "App.tsx"
    frontend.write_text("ui")
    initial = snapshot(tmp_path)
    assert snapshot(tmp_path, initial) == initial
    frontend.write_text("edited")
    assert snapshot(tmp_path, initial)[str(frontend)] != initial[str(frontend)]
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").mkdir()
    broken_skill = broken / ".agents/skills/macaw/SKILL.md"
    broken_skill.parent.mkdir(parents=True)
    broken_skill.write_text("Macaw")
    assert str(frontend) in snapshot(tmp_path, initial)


async def test_reminder_is_provider_safe_once_and_skips_new_repositories(monkeypatch):
    from openswe.middleware.design_reminder import DesignReminderMiddleware
    from openswe.run_config import RunConfig

    middleware = DesignReminderMiddleware()
    monkeypatch.setattr(
        RunConfig, "from_config", lambda config: type("Config", (), {"thread_id": "thread"})()
    )
    snapshots = iter(
        [
            {"/repo/.git": "head", "/repo/App.tsx": "1:old"},
            {
                "/repo/.git": "head",
                "/repo/App.tsx": "1:old",
                "/clone/.git": "head",
                "/clone/App.tsx": "1:new",
            },
            {
                "/repo/.git": "head",
                "/repo/App.tsx": "2:edited",
                "/clone/.git": "head",
                "/clone/App.tsx": "1:new",
            },
        ]
    )

    async def collect(thread_id):
        return next(snapshots)

    monkeypatch.setattr(middleware, "_snapshot", collect)
    state = {"messages": []}
    await middleware.abefore_agent(state, None, {})
    assert await middleware.abefore_model(state, None, {}) is None
    result = await middleware.abefore_model(state, None, {})
    assert result["messages"][0]["role"] == "user"
    assert await middleware.abefore_model(state, None, {}) is None
