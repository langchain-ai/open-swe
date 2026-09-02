"""Where a session's conversation lives.

A surface answers the handful of questions that differ between a Slack thread, a
Slack code channel and the web dashboard: where a reply goes, what the agent is
told about the place it is speaking, and which chrome the surface keeps in sync
with the work. Callers ask the surface instead of asking which kind it is.

Every question has a "nothing to do" answer, which is what the dashboard needs,
so :class:`Surface` is both the base class and the web surface.
"""

from typing import Any, Literal

SurfaceKind = Literal["slack_thread", "slack_channel", "web"]


class Surface:
    """A conversation surface with no chrome and no posting rules."""

    kind: SurfaceKind = "web"

    #: Whether the surface shows the user that the agent is working, and so has
    #: to be told when a turn starts and ends.
    reports_activity: bool = False

    #: Whether the surface renders session chrome — context, resources, views —
    #: that the work has to be published into. Callers check this before doing
    #: work whose only purpose is to fill it.
    has_chrome: bool = False

    def prompt_section(self) -> str:
        """Operational context describing this surface to the agent."""
        return ""

    def reply_target(self) -> str | None:
        """The Slack ``thread_ts`` a reply belongs in, if any."""
        return None

    def web_link_thread_id(self, thread_id: str) -> str | None:
        """The agent thread a posted message's web link should point at."""
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


WEB_SURFACE = Surface()
