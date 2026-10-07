"""Shared formatting for messages Open SWE posts on its own initiative.

Automatic notices are written in the third person ("Open SWE …") so a reader
scanning a Slack thread or PR can tell them apart from the agent's own replies,
and they all lead with the same icon so they are recognizable at a glance.
"""

WARNING_ICON = "⚠️"


def warning(text: str) -> str:
    return f"{WARNING_ICON} {text}"
