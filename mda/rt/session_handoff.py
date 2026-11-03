"""Browser sessions MCP results carried, kept in the run's memory for ``browser_sign_in``.

The model sees only a handle; each session is installed at most once, and a run that
never installs one drops it when it ends.
"""

import json
import logging
import re

from pydantic import BaseModel, Field, JsonValue, ValidationError
from stagehand import Page

from rt.backend import BackendCallError, OpenSweBackend, RunLocal

logger = logging.getLogger(__name__)

_SESSION_HOSTS = re.compile(
    r"(?:(?:beta|eu|apac|aws|dev)\.)?smith\.langchain\.com"
    r"|pr-[0-9]+(?:-fe)?\.review\.smith\.langchain\.com"
)
INSTALLED = "Signed in."


class HeldSession(BaseModel):
    """A site's sign-in, as the site keeps it in its ``localStorage``."""

    host: str
    storage_key: str
    session: dict[str, JsonValue] = Field(repr=False)

    @property
    def on_langsmith(self) -> bool:
        return _SESSION_HOSTS.fullmatch(self.host) is not None

    async def install(self, page: Page) -> str:
        """Sign ``page``'s browser in on the session's host, then load it signed in."""
        origin = f"https://{self.host}"
        try:
            await page.goto(origin)
            await page.evaluate(
                f"(() => {{ if (location.origin !== {json.dumps(origin)}) "
                f"throw new Error('Wrong session origin'); "
                f"localStorage.setItem({json.dumps(self.storage_key)}, "
                f"{json.dumps(json.dumps(self.session))}); }})()"
            )
            await page.goto(origin)
        except Exception as exc:  # noqa: BLE001 - reported to the model, without the session
            # What failed can quote the script, and so the session: only its type is logged.
            logger.warning(
                "Could not install a held browser session",
                extra={"error_type": type(exc).__name__},
            )
            return "Could not install the browser session."
        return INSTALLED


class HeldSessions(dict[str, dict[str, JsonValue]]):
    """The run's sessions, by handle."""

    @classmethod
    def of(cls, backend: OpenSweBackend) -> HeldSessions | None:
        """The sessions of the run in progress; None outside an Open SWE run."""
        try:
            return _HELD.get(backend.run)
        except BackendCallError:
            return None

    def take(self, handle: str) -> HeldSession | None:
        """The session under ``handle``, which no later call can take again."""
        held = self.pop(handle, None)
        if held is None:
            return None
        try:
            return HeldSession.model_validate(held)
        except ValidationError as exc:
            logger.warning(
                "An MCP result held a malformed browser session",
                extra={"error_count": exc.error_count()},
            )
            return None


_HELD: RunLocal[HeldSessions] = RunLocal(HeldSessions)
