"""What kind of Slack session a conversation lives in.

Slack has two: a thread inside a shared channel, and a code channel that is one
session end to end. They answer the same handful of questions differently —
where a reply goes, what the agent is told about the place it is speaking,
which chrome the surface keeps in sync with the work — and callers ask the
surface instead of asking which kind it is.

This is a Slack abstraction, not a general one. The web dashboard is not a
third kind: it reads a thread, whichever Slack session that thread belongs to,
so it is a view onto a surface. A session with no Slack conversation behind it
has no surface at all, which is why `slack_surface` can answer with nothing.

Every question has a "nothing to do" answer, so a surface only overrides what
it actually does.
"""

from typing import Any

from agent.source_context import SlackSurfaceKind


class SlackSurface:
    """A Slack session with no chrome and no posting rules."""

    kind: SlackSurfaceKind = "slack_thread"

    #: Whether the surface shows the user that the agent is working, and so has
    #: to be told when a turn starts and ends.
    reports_activity: bool = False

    #: Whether the agent's own words reach this surface without a posting tool,
    #: because something mirrors the run into it.
    projects_transcript: bool = False

    #: Whether the surface renders session chrome — a status, a title, a context
    #: bar — that the work has to keep current.
    has_chrome: bool = False

    def prompt_section(self) -> str:
        """Operational context describing this surface to the agent."""
        return ""

    def reply_target(self) -> str | None:
        """The Slack ``thread_ts`` a reply belongs in, if any."""
        return None

    def viewer_link_thread_id(self, thread_id: str) -> str | None:
        """The agent thread a posted message's "Open in Web" footer points at."""
        return thread_id or None

    async def begin_turn(self) -> None:
        """Signal that the agent started working."""
        return None

    async def end_turn(self) -> None:
        """Signal that the agent stopped working and awaits input."""
        return None

    async def start_session(self, *, repo: dict[str, Any] | None, thread_id: str) -> None:
        """Set up surface chrome for a session's first turn."""
        return None

    async def sync_context(
        self,
        *,
        repo: dict[str, Any] | None,
        thread_id: str,
        branch: str = "",
        pr_url: str = "",
    ) -> None:
        """Bring the surface's view of the work up to date."""
        return None

    async def set_resource(self, resource: dict[str, Any]) -> None:
        """Attach an external resource (a pull request, a dashboard) to the surface."""
        return None

    async def publish_diff(self, content: str, *, base_branch: str, head_branch: str) -> None:
        """Show a unified diff of the work."""
        return None

    async def set_title(self, title: str) -> None:
        """Rename the session."""
        return None
