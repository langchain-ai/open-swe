"""Agent connections the Slack channel reserves for its app."""

from __future__ import annotations

SLACK_BOT_TOKEN_SLUG = "mda/slack-bot-token"  # noqa: S105 - a connection name, not a secret
"""Reserved agent connection: the Slack app's bot token, to call Slack's Web API as the app."""
