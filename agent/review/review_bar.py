"""The finding bar every Open SWE reviewer applies, shared verbatim.

The PR reviewer and a thread's self-review of its own PR must reject and accept
the same findings; a second copy of these rules would drift.
"""

from agent.prompts import load_prompt, render_prompt

SEVERITY_RUBRIC_SECTION = load_prompt("reviewer/severity-rubric.md")


def render_finding_bar(historical_review_guidance: str = "") -> str:
    """Render the shared finding bar and review workflow."""
    return render_prompt(
        "reviewer/finding-bar.md",
        historical_review_guidance=historical_review_guidance,
    )
