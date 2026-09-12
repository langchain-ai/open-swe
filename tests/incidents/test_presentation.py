import pytest

from agent.incidents.models import Evidence, Hypothesis, IncidentReport
from agent.incidents.presentation import report_message


def test_digest_groups_citations_and_keeps_details_in_report():
    report = IncidentReport(
        summary="Historical replay: 429s exceeded 10%. [slack:1, slack:2] Recovered later. [slack:2]",
        impact="Customer impact is unknown. [slack:1]",
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
    assert "Historical replay" in text and "Customer impact is unknown" in text
    assert text.count("https://slack.com/archives/C1/p1") == 1
    assert text.count("https://slack.com/archives/C1/p2") == 1
    assert "p3" not in text and "[slack:" not in text
    assert "Retry storm" not in text and "Who owns retries" not in text
    assert "Datadog access unavailable" in text
    assert "Full report &amp; postmortem" in text
    assert blocks[0]["type"] == "header"
    assert len(text) < 700
    assert len(report.questions) == 2 and len(report.hypotheses) == 1


def test_digest_bounds_untrusted_text_without_mentions_or_unsafe_links():
    report = IncidentReport(
        summary="<!channel> <@U1> & bad [bad] " + "Long observation. " * 800,
        impact="Large impact. " * 1000,
        gaps=["Missing access. " * 1000],
        evidence=[Evidence(id="bad", source="slack", summary="Bad", url="javascript:alert(1)")],
    )
    text, blocks = report_message(report, report.summary, None)
    assert "<!channel>" not in text and "<@U1>" not in text
    assert "&lt;!channel&gt;" in text and "javascript:" not in text
    assert len(text) < 1800
    assert all(len(block.get("text", {}).get("text", "")) <= 3000 for block in blocks)


@pytest.mark.parametrize("character", ["&", "<", ">"])
def test_escaping_cannot_exceed_slack_block_limits(character):
    report = IncidentReport(summary=character * 10000)
    text, blocks = report_message(report, report.summary, None, reason="answer")
    assert len(blocks[1]["text"]["text"]) <= 2400
    assert text.endswith(";…")
