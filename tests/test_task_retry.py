from __future__ import annotations

import json

import pytest

from agent.middleware.task_retry import task_on_failure, task_retry_on


class _HTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _InvalidPromptError(Exception):
    body = {"error": {"type": "invalid_request_error", "code": "invalid_prompt"}}


def test_task_retry_on_transient_status() -> None:
    assert task_retry_on(_HTTPError(429)) is True
    assert task_retry_on(_HTTPError(503)) is True
    assert task_retry_on(_HTTPError(400)) is False


def test_task_on_failure_returns_model_fixable_error() -> None:
    payload = json.loads(task_on_failure(_InvalidPromptError("bad prompt")))

    assert payload["status"] == "failed"
    assert payload["source"] == "subagent"
    assert payload["error"]["code"] == "invalid_prompt"


def test_task_on_failure_reraises_unrecoverable_error() -> None:
    exc = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        task_on_failure(exc)
