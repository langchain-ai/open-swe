"""Regression coverage for native shell detection and cancellation of real commands."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from agent.sandboxes.environment import probe_execution_environment
from agent.sandboxes.local_execution import (
    AsyncLocalShellBackend,
    _network_escape,
    _outside_sandbox,
)


def _backend(root: Path, *, limit: int = 100_000) -> AsyncLocalShellBackend:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    # These commands name the test interpreter by absolute path.
    return AsyncLocalShellBackend(
        root_dir=root,
        env=env,
        max_output_bytes=limit,
        extra_roots=[os.path.dirname(sys.executable)],
    )


async def _wait_for_file(path: Path) -> None:
    async with asyncio.timeout(10):
        while not path.exists():
            await asyncio.sleep(0.02)


async def test_probe_reports_sandbox_interpreter_and_directory_with_spaces(tmp_path: Path) -> None:
    root = tmp_path / "project with spaces"
    root.mkdir()
    environment = await probe_execution_environment(_backend(root))
    assert environment.system != "unknown"
    assert environment.shell_family == ("cmd" if os.name == "nt" else "posix")
    assert environment.python_executable is not None
    assert Path(environment.python_executable).resolve() == Path(sys.executable).resolve()
    assert environment.working_dir == str(root)
    assert environment.git_version is not None


async def test_execution_drains_large_output_but_bounds_retained_bytes(tmp_path: Path) -> None:
    response = await _backend(tmp_path, limit=64).aexecute(
        f'"{sys.executable}" -c "print(chr(120)*1000000)"', timeout=10
    )
    assert response.exit_code == 0
    assert response.truncated is True
    assert response.output.startswith("x" * 64)
    assert len(response.output) < 1000


async def test_timeout_retains_prior_output(tmp_path: Path) -> None:
    response = await _backend(tmp_path).aexecute(
        f'"{sys.executable}" -u -c "import time; print(123,flush=True); time.sleep(60)"',
        timeout=1,
    )
    assert response.exit_code == 124
    assert "123" in response.output


async def test_cancellation_terminates_descendant_before_it_can_write(tmp_path: Path) -> None:
    child = tmp_path / "child.py"
    child.write_text(
        "from pathlib import Path\nimport time\n"
        "Path('child-ready').touch()\ntime.sleep(1.5)\nPath('escaped').touch()\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess,sys\nsubprocess.run([sys.executable,'child.py'],check=True)\n",
        encoding="utf-8",
    )
    task = asyncio.create_task(_backend(tmp_path).aexecute(f'"{sys.executable}" parent.py'))
    await _wait_for_file(tmp_path / "child-ready")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.7)
    assert not (tmp_path / "escaped").exists()


async def test_command_naming_a_path_outside_the_sandbox_is_not_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "marker.txt"
    marker.write_text("secret contents", encoding="utf-8")

    response = await _backend(project).aexecute(f'type "{marker}"', timeout=10)

    assert response.exit_code == 126
    assert str(marker) in response.output
    assert "secret contents" not in response.output


def test_a_path_that_does_not_exist_yet_is_judged_by_its_parent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert _outside_sandbox(f'cd "{project}" && git clone url eval-1', [project]) is None
    assert _outside_sandbox(f'cd "{tmp_path}" && ls', [project]) is not None


def test_a_url_is_not_read_as_an_absolute_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert _outside_sandbox("git clone https://example.com/repo.git x", [project]) is None
    assert _outside_sandbox('git clone "https://example.com/repo.git" x', [project]) is None
    # A real UNC path is still outside a local project.
    assert _outside_sandbox(r"type \\server\share\file", [project]) is not None


def test_a_relative_path_that_climbs_out_is_not_run(tmp_path: Path) -> None:
    """`..` reaches the parent just as surely as its absolute path does.

    The eval workspace keeps the task set with its expected patches one level
    above the checkout, so a run that lists the parent can read the answer.
    """
    project = tmp_path / "work" / "project"
    project.mkdir(parents=True)
    (tmp_path / "work" / "tasks.jsonl").write_text("gold patch", encoding="utf-8")

    assert _outside_sandbox(f'cd "{project}" && dir ..', [project]) == ".."
    assert _outside_sandbox(f'cd "{project}" && cd .. && type tasks.jsonl', [project]) == ".."
    assert _outside_sandbox("dir ../..", [project]) == "../.."
    # A path that stays inside the project is still ordinary work.
    assert _outside_sandbox(f'cd "{project}" && dir src && cd src && dir', [project]) is None


def test_an_evaluation_run_may_clone_once_and_nothing_more() -> None:
    clone = "git clone https://ghfast.top/https://github.com/pallets/markupsafe.git eval-x"
    assert _network_escape(clone) is None
    assert _network_escape(rf'cd /d "D:\work" && {clone}') is None
    # Looking up the issue, or pulling the upstream fix, is how a model
    # answers the benchmark from outside the checkout.
    for blocked in (
        "git fetch origin main",
        "git pull",
        "git remote -v",
        "curl -s https://api.github.com/repos/pallets/markupsafe/issues/533",
        f"{clone} && git fetch origin main",
        f"{clone}; curl https://api.github.com/x",
        "wget https://example.com/x",
    ):
        assert _network_escape(blocked) is not None, blocked
    # Ordinary work stays allowed.
    for allowed in ("python -m pytest tests -q", "pip install -e .", "git -C eval-x status"):
        assert _network_escape(allowed) is None, allowed


async def test_an_offline_sandbox_refuses_a_network_command(tmp_path: Path) -> None:
    backend = AsyncLocalShellBackend(root_dir=tmp_path, offline=True)
    response = await backend.aexecute("curl -s https://api.github.com/repos/x", timeout=10)
    assert response.exit_code == 126
    assert "reaches the network" in response.output


async def test_command_naming_an_allowed_root_runs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "marker.txt").write_text("inside", encoding="utf-8")

    response = await _backend(project).aexecute('type "marker.txt"', timeout=10)
    assert response.exit_code == 0
    assert "inside" in response.output
    # The test interpreter's own directory is an allowed root, so another
    # command may name a sibling of the project through it.
    interpreter_dir = Path(sys.executable).parent
    response = await _backend(project).aexecute(f'cd /d "{interpreter_dir}" && echo ok', timeout=10)
    assert response.exit_code == 0


async def test_successful_command_can_start_an_explicit_background_process(tmp_path: Path) -> None:
    (tmp_path / "child.py").write_text(
        "from pathlib import Path\nimport time\ntime.sleep(0.5)\nPath('finished').touch()\n",
        encoding="utf-8",
    )
    (tmp_path / "parent.py").write_text(
        "import subprocess,sys\nsubprocess.Popen([sys.executable,'child.py'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n",
        encoding="utf-8",
    )
    result = await _backend(tmp_path).aexecute(f'"{sys.executable}" parent.py', timeout=10)
    assert result.exit_code == 0
    await _wait_for_file(tmp_path / "finished")
