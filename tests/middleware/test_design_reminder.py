import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from openswe.middleware.design_reminder import DesignReminderMiddleware
from openswe.middleware.design_snapshot import snapshot
from openswe.run_config import RunConfig
from openswe.sandboxes.state import SANDBOX_BACKENDS


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
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    assert not any(Path(path).is_relative_to(link) for path in snapshot(tmp_path))


class _LimitedShellBackend(LocalShellBackend):
    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if len(command.encode()) >= 128 * 1024:
            raise OSError(errno.E2BIG, "Argument list too long")
        return await super().aexecute(command, timeout=timeout)


async def test_reminder_handles_large_snapshots_and_unrelated_repository_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "app"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    skill = repo / ".agents/skills/macaw/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Macaw")
    for index in range(3_200):
        (repo / f"{index:04d}-{'a' * 210}.tsx").write_text("ui")
    frontend = repo / "App.tsx"
    frontend.write_text("already dirty")
    broken = workspace / "broken"
    broken.mkdir()
    (broken / ".git").mkdir()
    broken_skill = broken / ".agents/skills/macaw/SKILL.md"
    broken_skill.parent.mkdir(parents=True)
    broken_skill.write_text("Macaw")
    assert len(json.dumps(snapshot(workspace)).encode()) > 1024 * 1024
    backend = _LimitedShellBackend(
        root_dir=tmp_path,
        virtual_mode=False,
        max_output_bytes=1024 * 1024,
        env={"PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.defpath}"},
    )
    monkeypatch.setattr(backend, "get_work_dir", lambda: str(workspace), raising=False)
    monkeypatch.setitem(SANDBOX_BACKENDS, "thread", backend)
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: RunConfig(thread_id="thread"))
    middleware = DesignReminderMiddleware()
    state = {"messages": []}
    await middleware.abefore_agent(state, None)
    assert await middleware.abefore_model(state, None) is None
    frontend.write_text("edited")
    result = await middleware.abefore_model(state, None)
    assert result is not None
    assert result["messages"][0]["role"] == "user"


async def test_reminder_is_provider_safe_once_and_skips_new_repositories(monkeypatch):
    middleware = DesignReminderMiddleware()
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: RunConfig(thread_id="thread"))
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
            {"/repo/.git": "head", "/repo/App.tsx": "2:edited"},
            {"/repo/.git": "head", "/repo/App.tsx": "3:edited again"},
        ]
    )

    async def collect(thread_id):
        return next(snapshots)

    monkeypatch.setattr(middleware, "_snapshot", collect)
    state = {"messages": []}
    await middleware.abefore_agent(state, None)
    assert await middleware.abefore_model(state, None) is None
    result = await middleware.abefore_model(state, None)
    assert result is not None
    assert result["messages"][0]["role"] == "user"
    assert await middleware.abefore_model(state, None) is None
    state["messages"].extend(result["messages"])
    await middleware.abefore_agent(state, None)
    assert await middleware.abefore_model(state, None) is None
