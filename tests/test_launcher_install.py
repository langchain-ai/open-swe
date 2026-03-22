from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path("/home/tom/ai/open-swe")
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_launchers.sh"


def make_fake_repo(root: Path, name: str) -> Path:
    repo = root / name
    (repo / "scripts").mkdir(parents=True)
    (repo / "local_fix_agent.py").write_text(
        "import os, sys\n"
        "print('CWD=' + os.getcwd())\n"
        "print('ARGS=' + ' '.join(sys.argv[1:]))\n"
    )
    (repo / "scripts" / "fixpublish.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo \"PUBLISH_CWD=$PWD\"\n"
        "echo \"PUBLISH_ARGS=$*\"\n"
    )
    (repo / "scripts" / "fixpublish.sh").chmod(0o755)
    return repo


def install_launchers(bin_dir: Path, repo_dir: Path, path_value: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if path_value:
        env["PATH"] = path_value
    return subprocess.run(
        [str(INSTALL_SCRIPT), "--bin-dir", str(bin_dir), "--repo", str(repo_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def test_install_script_creates_executables_and_path_guidance(tmp_path: Path) -> None:
    fake_repo = make_fake_repo(tmp_path, "repo")
    bin_dir = tmp_path / "bin"

    completed = install_launchers(bin_dir, fake_repo, path_value="/usr/bin")

    assert (bin_dir / "fixapp").exists()
    assert (bin_dir / "fixpublish").exists()
    assert (bin_dir / "fixit").exists()
    assert os.access(bin_dir / "fixapp", os.X_OK)
    assert os.access(bin_dir / "fixpublish", os.X_OK)
    assert os.access(bin_dir / "fixit", os.X_OK)
    assert f'export PATH="{bin_dir}:$PATH"' in completed.stdout
    assert ">> ~/.bashrc" in completed.stdout
    assert ">> ~/.zshrc" in completed.stdout


def test_fixit_launcher_passes_args_and_uses_default_repo(tmp_path: Path) -> None:
    fake_repo = make_fake_repo(tmp_path, "repo")
    bin_dir = tmp_path / "bin"
    install_launchers(bin_dir, fake_repo)

    completed = subprocess.run(
        [str(bin_dir / "fixit"), "--probe-url", "https://example.com/data"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"CWD={fake_repo}" in completed.stdout
    assert "ARGS=--probe-url https://example.com/data" in completed.stdout


def test_fixapp_launcher_constructs_interactive_command(tmp_path: Path) -> None:
    fake_repo = make_fake_repo(tmp_path, "repo")
    bin_dir = tmp_path / "bin"
    install_launchers(bin_dir, fake_repo)

    completed = subprocess.run(
        [str(bin_dir / "fixapp"), "--help"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"CWD={fake_repo}" in completed.stdout
    assert "ARGS=--interactive --help" in completed.stdout


def test_fixpublish_launcher_uses_env_repo_override_and_passthrough(tmp_path: Path) -> None:
    default_repo = make_fake_repo(tmp_path, "default_repo")
    override_repo = make_fake_repo(tmp_path, "override_repo")
    bin_dir = tmp_path / "bin"
    install_launchers(bin_dir, default_repo)

    env = os.environ.copy()
    env["OPEN_SWE_REPO"] = str(override_repo)
    completed = subprocess.run(
        [str(bin_dir / "fixpublish"), "--dry-run"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    assert f"PUBLISH_CWD={override_repo}" in completed.stdout
    assert "PUBLISH_ARGS=--dry-run" in completed.stdout


def test_launchers_detect_current_repo_before_default(tmp_path: Path) -> None:
    current_repo = make_fake_repo(tmp_path, "current_repo")
    bin_dir = tmp_path / "bin"
    install_launchers(bin_dir, tmp_path / "missing_default")

    completed = subprocess.run(
        [str(bin_dir / "fixit"), "--last"],
        cwd=str(current_repo),
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"CWD={current_repo}" in completed.stdout
    assert "ARGS=--last" in completed.stdout
