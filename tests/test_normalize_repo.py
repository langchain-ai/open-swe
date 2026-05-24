from __future__ import annotations

import pytest

from agent.dashboard.review_styles import normalize_repo_full_name


def test_normalize_repo_full_name_accepts_urls() -> None:
    assert normalize_repo_full_name("https://github.com/langchain-ai/langgraph") == (
        "langchain-ai/langgraph"
    )


def test_normalize_repo_full_name_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        normalize_repo_full_name("not-a-repo")
