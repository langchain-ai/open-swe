"""Tests for the cached-PR-context prompt section."""

from __future__ import annotations

from agent.prompt import construct_system_prompt


def test_cached_pr_context_section_omitted_by_default() -> None:
    prompt = construct_system_prompt(working_dir="/sandbox")
    assert "Cached PR Context" not in prompt


def test_cached_pr_context_section_omitted_for_malformed_payload() -> None:
    prompt = construct_system_prompt(
        working_dir="/sandbox",
        cached_pr_context={"pr_number": "not-an-int", "repo": {"owner": "o", "name": "n"}},
    )
    assert "Cached PR Context" not in prompt


def test_cached_pr_context_section_rendered_when_present() -> None:
    prompt = construct_system_prompt(
        working_dir="/sandbox",
        cached_pr_context={
            "pr_number": 28399,
            "repo": {"owner": "langchain-ai", "name": "langchain"},
            "branch": "open-swe/fix-28399",
            "age_seconds": 120,
            "thread_id": "thread-a",
        },
    )
    assert "Cached PR Context" in prompt
    assert "langchain-ai/langchain" in prompt
    assert "PR #28399" in prompt
    assert "open-swe/fix-28399" in prompt
    assert "skip the cold-start preamble" in prompt
