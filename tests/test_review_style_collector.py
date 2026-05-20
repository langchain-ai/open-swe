from __future__ import annotations

from agent.review_style_collector import (
    ReviewSample,
    ReviewStyleSamples,
    format_samples_for_analyzer,
)


def test_format_samples_for_analyzer_groups_by_reviewer() -> None:
    samples = ReviewStyleSamples(
        full_name="acme/widget",
        owner="acme",
        name="widget",
        top_reviewers=["alice", "bob"],
        samples=[
            ReviewSample(1, "alice", "review", "Missing nil check on line 42.", state="COMMENTED"),
            ReviewSample(2, "bob", "inline", "Race in cache update path.", path="cache.go"),
        ],
        prs_scanned=5,
        reviews_scanned=10,
    )
    text = format_samples_for_analyzer(samples)
    assert "acme/widget" in text
    assert "## Reviewer: @alice" in text
    assert "PR #1" in text
    assert "Race in cache" in text
