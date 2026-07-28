"""Tests for the docker sandbox backend (OPE-7) against a real local daemon.

Run:  .venv/bin/python -m pytest speedbay/tests/test_docker_sandbox.py -x -q

Skipped entirely when the docker daemon or the sandbox image is unavailable,
so the suite is safe in environments without docker.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from agent.integrations.docker_local import (
    DockerSandbox,
    _sweep_expired,
    create_docker_sandbox,
    validate_startup_config,
)


def _docker_ready() -> bool:
    try:
        validate_startup_config()
    except (ValueError, FileNotFoundError):
        return False
    return True


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="docker daemon or image unavailable")


@pytest.fixture(scope="module")
def sandbox():
    """One container for the whole module; removed at teardown."""
    sb = create_docker_sandbox()
    yield sb
    subprocess.run(["docker", "rm", "-f", sb.id], capture_output=True)


def test_execute_and_exit_codes(sandbox: DockerSandbox) -> None:
    ok = sandbox.execute("echo hello && uname -s")
    assert ok.exit_code == 0
    assert "hello" in ok.output and "Linux" in ok.output
    bad = sandbox.execute("exit 42")
    assert bad.exit_code == 42


def test_file_round_trip(sandbox: DockerSandbox) -> None:
    payload = b"round-trip \xf0\x9f\x8e\x89 with unicode and a null-free binary tail \x01\x02"
    [up] = sandbox.upload_files([("/workspace/sub/dir/file.bin", payload)])
    assert up.error is None
    [down] = sandbox.download_files(["/workspace/sub/dir/file.bin"])
    assert down.error is None and down.content == payload
    [missing] = sandbox.download_files(["/workspace/does-not-exist"])
    assert missing.error == "file_not_found" and missing.content is None


def test_reconnect_preserves_state(sandbox: DockerSandbox) -> None:
    marker = uuid.uuid4().hex
    sandbox.execute(f"echo {marker} > /workspace/marker.txt")
    reconnected = create_docker_sandbox(sandbox.id)
    assert reconnected.id == sandbox.id
    out = reconnected.execute("cat /workspace/marker.txt")
    assert marker in out.output


def test_git_auth_provisioned(sandbox: DockerSandbox) -> None:
    """The gh shim must override GH_TOKEN=dummy exactly as the prompt uses it."""
    res = sandbox.execute("GH_TOKEN=dummy gh api /repos/speedbay/warehouse --jq .full_name")
    assert res.exit_code == 0, res.output
    assert "speedbay/warehouse" in res.output


def test_commit_msg_hook_strips_attribution(sandbox: DockerSandbox) -> None:
    res = sandbox.execute(
        "rm -rf /tmp/hooktest && mkdir /tmp/hooktest && cd /tmp/hooktest && git init -q . && "
        "echo x > f && git add f && "
        "printf 'T-1: subject\\n\\nCo-authored-by: open-swe[bot] <x@y>\\n' > /tmp/msg && "
        "git commit -q -F /tmp/msg && git log -1 --format=%B"
    )
    assert res.exit_code == 0, res.output
    assert "Co-authored-by" not in res.output


def test_isolation_no_host_paths(sandbox: DockerSandbox) -> None:
    """Negative proof: host-only paths must not exist inside the container."""
    for host_path in ("/Users", "/opt/homebrew", "/private/etc/ssh"):
        res = sandbox.execute(f"test -e {host_path}")
        assert res.exit_code != 0, f"{host_path} is visible inside the sandbox"


def test_timeout_returns_124(sandbox: DockerSandbox) -> None:
    res = sandbox.execute("sleep 30", timeout=2)
    assert res.exit_code == 124
    assert "timed out" in res.output


def test_ttl_sweep_removes_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    sb = create_docker_sandbox()
    monkeypatch.setenv("DOCKER_SANDBOX_TTL_SECONDS", "0")
    _sweep_expired()
    proc = subprocess.run(
        ["docker", "container", "inspect", sb.id], capture_output=True
    )
    assert proc.returncode != 0, "expired container survived the sweep"
