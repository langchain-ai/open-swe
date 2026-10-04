from agent.incidents.models import IncidentReport
from agent.incidents.presentation import report_message


def test_model_summary_preserves_markdown_and_long_links_without_mentions():
    message = "**Finding**: [Logs](https://example.com/logs?x=1&y=2)\n" + "Details. " * 400
    report = IncidentReport(summary="Headline", slack_message=message + " <!channel> <@U1>")
    text, blocks = report_message(report, report.summary, "https://example.com/incident")
    assert message in text
    assert "<!channel>" not in text and "<@U1>" not in text
    assert "&lt;!channel&gt; &lt;@U1&gt;" in text
    assert "[View investigation](<https://example.com/incident>)" in text
    assert blocks[0]["text"] == text


def test_legacy_report_and_completion_use_fallback_without_unsafe_footer():
    report = IncidentReport(summary="Headline", slack_message="Current findings")
    text, _ = report_message(
        report, "Incident complete. Headline", "javascript:alert(1)", reason="completion"
    )
    assert text == "Incident complete. Headline"
    legacy = IncidentReport(summary="Legacy headline")
    text, _ = report_message(legacy, legacy.summary, None)
    assert text == "Legacy headline"
