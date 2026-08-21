"""Fixtures shared by the reviewer tests."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from support.publish_harness import PublishHarness

from agent.review.findings import Finding


@pytest.fixture
def publish_harness() -> Iterator[PublishHarness]:
    """Stub every boundary the publish flow touches, in one place."""
    harness = PublishHarness()

    async def _list_findings(_thread_id: str) -> list[Finding]:
        return harness.findings

    async def _get_thread_metadata(_thread_id: str) -> dict[str, Any]:
        return harness.metadata

    with (
        patch("agent.review.publish_flow.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.review.publish_flow.list_findings_async", _list_findings),
        patch("agent.review.reconcile.list_findings", _list_findings),
        patch("agent.review.publish_flow.get_thread_metadata", _get_thread_metadata),
        patch("agent.review.publish_flow.post_pull_request_review", harness.post_review),
        patch("agent.review.publish_flow.fetch_review_comments", harness.fetch_comments),
        patch("agent.review.publish_flow.set_reviewer_thread_metadata", harness.set_metadata),
        patch(
            "agent.review.publish_flow.resolve_threads_for_resolved_findings",
            harness.resolve_threads,
        ),
        patch("agent.review.publish_flow.post_slack_thread_reply", harness.slack_post),
    ):
        yield harness
