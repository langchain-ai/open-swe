from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_workspace


class _Result:
    def __init__(self, *, exit_code: int | None = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output


class _FakeSandbox:
    def __init__(self, result: _Result | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result or _Result()

    def execute(self, command: str, timeout: int | None = None) -> _Result:
        self.calls.append({"command": command, "timeout": timeout})
        return self.result


def _worker_input(**overrides: Any) -> dict[str, Any]:
    payload = {
        "issue_context": {
            "repository": {"owner": "example", "name": "sports-cms"},
            "branch": "delivery/sports-cms/eng-123",
            "base_branch": "main",
        },
        "sandbox_profile": {
            "provider": "langsmith",
            "worktree": {
                "path": "/workspace/worktrees/delivery-sports-cms-eng-123",
                "branch": "delivery/sports-cms/eng-123",
                "base_branch": "main",
            },
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_provisions_delivery_workspace_checkout_in_sandbox() -> None:
    sandbox = _FakeSandbox()

    result = await delivery_workspace.provision_delivery_workspace(
        sandbox,
        worker_input=_worker_input(),
        default_work_dir="/workspace",
    )

    assert result == {
        "status": "ready",
        "strategy": "sandbox_git_checkout",
        "repo": {"owner": "example", "name": "sports-cms"},
        "path": "/workspace/worktrees/delivery-sports-cms-eng-123",
        "branch": "delivery/sports-cms/eng-123",
        "base_branch": "main",
    }
    assert len(sandbox.calls) == 1
    assert sandbox.calls[0]["timeout"] == delivery_workspace.WORKSPACE_PROVISION_TIMEOUT_SECONDS
    command = sandbox.calls[0]["command"]
    assert "gh repo clone example/sports-cms" in command
    assert "git checkout --force -B" in command
    assert "delivery/sports-cms/eng-123" in command


@pytest.mark.asyncio
async def test_provisioning_reports_checkout_failure_without_claiming_ready() -> None:
    sandbox = _FakeSandbox(_Result(exit_code=128, output="fatal: could not fetch"))

    result = await delivery_workspace.provision_delivery_workspace(
        sandbox,
        worker_input=_worker_input(),
        default_work_dir="/workspace",
    )

    assert result == {
        "status": "failed",
        "reason": "checkout_failed",
        "exit_code": 128,
        "output": "fatal: could not fetch",
    }


@pytest.mark.asyncio
async def test_provisioning_requires_repository_context() -> None:
    sandbox = _FakeSandbox()

    result = await delivery_workspace.provision_delivery_workspace(
        sandbox,
        worker_input=_worker_input(issue_context={}),
        default_work_dir="/workspace",
    )

    assert result == {"status": "failed", "reason": "missing_repository"}
    assert sandbox.calls == []
