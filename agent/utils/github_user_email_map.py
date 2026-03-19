"""Mapping of GitHub usernames to emails.

Users listed here are allowed to trigger the agent and have their
comments treated as trusted in the prompt. The email value is only
used for LangSmith per-user OAuth (not applicable in bot-token-only mode).
"""

GITHUB_USER_EMAIL_MAP: dict[str, str] = {
    "slarson-redo": "slarson@redo.com",
}
