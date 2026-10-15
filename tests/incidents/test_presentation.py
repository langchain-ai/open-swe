import pytest

from openswe.incidents.models import Evidence, IncidentReport
from openswe.incidents.presentation import report_message


def test_investigation_bounds_untrusted_sections_without_mentions_or_unsafe_links():
    report = IncidentReport(
        summary="Long observation. " * 800,
        problem="<!channel> <@U1> & bad [bad] " + "Long observation. " * 800,
        previous_occurrence="Seen before. " * 1000,
        impact="Large impact. " * 1000,
        cause="Root cause. " * 1000,
        next_steps=["Do the thing. " * 1000] * 5,
        gaps=["Missing access. " * 1000],
        evidence=[Evidence(id="bad", source="slack", summary="Bad", url="javascript:alert(1)")],
    )
    text, blocks = report_message(report, report.summary, None)
    assert "<!channel>" not in text and "<@U1>" not in text
    assert "&lt;!channel&gt;" in text and "javascript:" not in text
    # Every section is over budget at once, so this is the widest the format can get.
    assert len(text) < 6000
    assert all(len(block.get("text", {}).get("text", "")) <= 3000 for block in blocks)
    # Only the three highest-priority steps are published.
    assert "\n4. " not in text
    assert blocks[-1]["text"]["text"].startswith("*TLDR*\n")
    assert len(blocks[-1]["text"]["text"]) <= 3000


def test_investigation_ends_with_tldr_before_sources():
    summary = (
        "TTL upgrades are delayed because the ClickHouse upsert of prepared runs repeatedly "
        "exceeds its context deadline, occupying worker slots while affected batches retry. "
        "The underlying trigger is still unknown; the earlier worker restart is only a "
        "correlation, not a confirmed root cause."
    )
    report = IncidentReport(
        summary=f"{summary} [tool:1]",
        problem="Upload failures.",
        cause="Timeouts observed; trigger unproven.",
        next_steps=["Check upload latency."],
        evidence=[
            Evidence(id="tool:1", source="tool", summary="Timeouts", url="https://example.com")
        ],
    )
    text, blocks = report_message(report, report.summary, None)
    assert len(summary) > 250
    assert blocks[-2]["text"]["text"] == f"*TLDR*\n{summary}"
    assert text.index("*Steps to solve*") < text.index("*TLDR*") < text.index("Sources:")


@pytest.mark.parametrize("character", ["&", "<", ">"])
def test_escaping_cannot_exceed_slack_block_limits(character):
    report = IncidentReport(summary=character * 10000)
    text, blocks = report_message(report, report.summary, None, reason="answer")
    assert len(blocks[0]["text"]["text"]) <= 2500
    assert text.endswith(";…")
