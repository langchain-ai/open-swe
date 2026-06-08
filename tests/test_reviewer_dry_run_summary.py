"""Reviewer prompt + publish_review docstring contract for eval-mode dry-runs.

The reviewer must not claim that the PR review was published when
`publish_review` short-circuits to its eval/benchmark dry-run path and
returns ``{"dry_run": true, "review_id": null, ...}``. This is enforced
by surfacing the contract in two places the model actually reads: the
tool docstring and the reviewer system prompt.
"""

from __future__ import annotations

from agent.reviewer import REVIEWER_PROMPT_TEMPLATE
from agent.tools.publish_review import publish_review

FORBIDDEN_CLAIM_WORDS = ("published", "posted", "submitted")


def test_publish_review_docstring_documents_dry_run() -> None:
    doc = publish_review.__doc__ or ""
    assert "dry_run" in doc
    assert "review_id" in doc
    assert "simulated publish" in doc.lower()
    assert "MUST NOT claim" in doc


def test_reviewer_prompt_has_closing_summary_rule() -> None:
    assert "Closing summary rules" in REVIEWER_PROMPT_TEMPLATE
    assert "dry_run" in REVIEWER_PROMPT_TEMPLATE
    assert "review_id" in REVIEWER_PROMPT_TEMPLATE
    assert "Simulated publish (eval mode) — review not posted to GitHub" in REVIEWER_PROMPT_TEMPLATE
    for word in FORBIDDEN_CLAIM_WORDS:
        assert word in REVIEWER_PROMPT_TEMPLATE, (
            f"prompt should warn the agent against the word {word!r} on dry-run"
        )


def test_reviewer_prompt_allows_real_publish_claim_when_review_id_present() -> None:
    assert "numeric value" in REVIEWER_PROMPT_TEMPLATE
    assert "absent or `false`" in REVIEWER_PROMPT_TEMPLATE
