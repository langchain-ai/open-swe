import subprocess
from pathlib import Path

from openswe.middleware.design_snapshot import snapshot


def test_snapshot_observes_native_and_shell_edits_without_dirty_false_positives(tmp_path: Path):
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
    frontend.write_text("native edit")
    native = snapshot(tmp_path)
    assert native[str(frontend)] != initial[str(frontend)]
    subprocess.run(["sh", "-c", 'printf shell > "$1"', "sh", str(frontend)], check=True)
    assert snapshot(tmp_path)[str(frontend)] != native[str(frontend)]
    frontend.unlink()
    assert str(frontend) not in snapshot(tmp_path)


def test_snapshot_requires_design_guidance_and_ignores_gitignored_files(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "App.tsx").write_text("ui")
    assert snapshot(tmp_path) == {}
    skill = tmp_path / ".claude/skills/macaw/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Macaw")
    (tmp_path / ".gitignore").write_text("ignored.tsx\n")
    (tmp_path / "ignored.tsx").write_text("ignored")
    assert list(snapshot(tmp_path)) == [str(tmp_path / "App.tsx")]
