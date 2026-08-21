"""The stubbed boundaries a reviewer publish-flow test runs against."""

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

from agent.review.findings import Finding


@dataclass
class PublishHarness:
    """The boundaries ``agent.review.publish_flow.publish_review`` talks to.

    Findings storage, the GitHub Reviews API, Slack and the resolved-thread
    sweep are stubbed together because the flow touches all of them on every
    path; a test that cares about one of them configures or asserts on that
    attribute and leaves the rest alone.
    """

    findings: list[Finding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    post_review: AsyncMock = field(default_factory=lambda: AsyncMock(return_value={"id": 555}))
    fetch_comments: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=[]))
    set_metadata: AsyncMock = field(default_factory=AsyncMock)
    resolve_threads: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=0))
    slack_post: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=True))

    def store(self, *findings: Finding) -> list[Finding]:
        """Seed the reviewer thread's findings and return the stored list."""
        self.findings = list(findings)
        return self.findings
