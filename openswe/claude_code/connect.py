"""Connecting a machine's Claude Code: its token, and the hooks settings that present it."""

from dataclasses import dataclass
from typing import Self
from uuid import UUID

from pydantic import JsonValue

from openswe.claude_code.hooks import MIRRORED_EVENTS
from openswe.claude_code.sync_tokens import ClaudeCodeSyncToken
from openswe.utils.dashboard_links import dashboard_api_base_url

HOOKS_PATH = "/dashboard/api/claude-code/hooks"
# Hooks run inline in Claude Code, so a slow mirror must never hold up the session.
_HOOK_TIMEOUT_SECONDS = 5
# Claude Code holds each batch of a message off the screen until this hook answers.
_DISPLAY_HOOK_TIMEOUT_SECONDS = 2


@dataclass(frozen=True)
class Connection:
    """A machine's freshly minted token: the only time its secret, and so its settings, exist."""

    token: ClaudeCodeSyncToken
    secret: str

    @classmethod
    async def open(cls, *, user_id: UUID, machine_name: str) -> Self:
        token, secret = await ClaudeCodeSyncToken.create(user_id=user_id, name=machine_name)
        return cls(token=token, secret=secret)

    @property
    def settings(self) -> dict[str, JsonValue]:
        """The ``hooks`` block of a Claude Code ``settings.json`` that mirrors every session."""
        return {
            "hooks": {
                event: [{"hooks": [self._handler(event)]}] for event in sorted(MIRRORED_EVENTS)
            }
        }

    def _handler(self, event: str) -> dict[str, JsonValue]:
        return {
            "type": "http",
            "url": dashboard_api_base_url() + HOOKS_PATH,
            "timeout": _DISPLAY_HOOK_TIMEOUT_SECONDS
            if event == "MessageDisplay"
            else _HOOK_TIMEOUT_SECONDS,
            "headers": {"Authorization": f"Bearer {self.secret}"},
        }
