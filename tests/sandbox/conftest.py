from unittest.mock import AsyncMock

import pytest

from agent.sandboxes import lifecycle


@pytest.fixture(autouse=True)
def _threads_record_no_token_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most threads carry no recorded scope; tests of the scope patch this themselves."""
    monkeypatch.setattr(lifecycle, "thread_token_repositories", AsyncMock(return_value=None))
