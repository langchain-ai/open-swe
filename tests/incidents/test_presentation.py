import pytest

from agent.incidents.models import Evidence, Hypothesis, IncidentReport
from agent.incidents.presentation import report_message


def test_investigation_publishes_named_sections_and_keeps_details_in_report():
    report = IncidentReport(
        summary="Historical replay: 429s exceeded 10%. [slack:1]",
        problem="The checkout endpoint breached its 5xx SLO. [slack:1]",
        previous_occurrence="INC-1714 raised the same alert on Sept 18. [slack:2]",
        impact="Customer impact is unknown. [slack:1]",
        cause="Retries saturated the upstream connection pool. [slack:2]",
        next_steps=[
            "Review the retry policy before changing the monitor. [slack:2]",
            "Re-check the monitor after the change. [slack:1]",
        ],
        hypotheses=[Hypothesis(title="Retry storm")],
        questions=["Who owns retries?", "Was there a deploy?"],
        gaps=["Datadog access unavailable.", "Repository access unavailable."],
        evidence=[
            Evidence(
                id=f"slack:{i}",
                source="slack",
                summary="Alert",
                url=f"https://slack.com/archives/C1/p{i}",
            )
            for i in range(1, 4)
        ],
    )
    text, blocks = report_message(report, report.summary, "https://swe.example/incidents/I1")
    assert text.startswith("*Investigation*")
    for label in ("Problem", "Previous occurrence", "Impact", "Cause", "Steps to solve"):
        assert f"*{label}*" in text
    assert "The checkout endpoint breached" in text
    assert "INC-1714 raised the same alert" in text
    assert "Customer impact is unknown" in text
    assert "Retries saturated" in text
    assert "1. Review the retry policy" in text and "2. Re-check the monitor" in text
    # The headline belongs to the dashboard and the postmortem. Repeating it here is the
    # duplicated-summary noise responders asked us to stop sending.
    assert "Historical replay" not in text
    assert text.count("https://slack.com/archives/C1/p1") == 1
    assert text.count("https://slack.com/archives/C1/p2") == 1
    assert "p3" not in text and "[slack:" not in text
    assert "Retry storm" not in text and "Who owns retries" not in text
    assert "Datadog access unavailable" not in text
    assert "View investigation" in text
    assert blocks[0]["type"] == "section"
    assert len(report.questions) == 2 and len(report.hypotheses) == 1


def test_investigation_without_sections_falls_back_to_the_headline():
    report = IncidentReport(summary="Errors began at 09:04")
    text, _ = report_message(report, report.summary, None)
    assert "Errors began at 09:04" in text
    assert text.strip() != "*Investigation*"


def test_answers_do_not_repeat_suggestions_from_an_earlier_report():
    report = IncidentReport(summary="Earlier finding", next_steps=["Earlier suggestion"])
    text, _ = report_message(report, "Answer to the responder", None, reason="answer")
    assert "Answer to the responder" in text
    assert "Earlier suggestion" not in text


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
    assert len(text) < 3200
    assert all(len(block.get("text", {}).get("text", "")) <= 3000 for block in blocks)
    # Only the three highest-priority steps are published.
    assert "\n4. " not in text


@pytest.mark.parametrize("character", ["&", "<", ">"])
def test_escaping_cannot_exceed_slack_block_limits(character):
    report = IncidentReport(summary=character * 10000)
    text, blocks = report_message(report, report.summary, None, reason="answer")
    assert len(blocks[0]["text"]["text"]) <= 2500
    assert text.endswith(";…")
