"""Ready the thread's box for the run's git work as MDA creates or adopts it.

MDA calls ``sandbox`` each time it resolves the box: as it creates it, or first
adopts it in this process, and again on each later run only to refresh
credentials, of which this connector has none. So the box gets the bot's git
identity and its work dir once per process, never on a turn's path.
"""

import logging
import shlex
from typing import Final

from managed_deepagents import Connector, SandboxContext
from runtime_api import box

logger = logging.getLogger(__name__)

_SETUP: Final = (
    f"mkdir -p {shlex.quote(box.WORK_DIR)} && test -w {shlex.quote(box.WORK_DIR)}; "
    f"git config --global user.name {shlex.quote(box.GIT_NAME)} && "
    f"git config --global user.email {shlex.quote(box.GIT_EMAIL)}"
)


class BoxSetup(Connector):
    kind = "box_setup"

    def sandbox(self, ctx: SandboxContext) -> None:
        if ctx.mode == "credentials":
            return
        result = ctx.backend.execute(_SETUP)
        if result.exit_code:
            logger.warning(
                "Could not ready the thread's box",
                extra={"exit_code": result.exit_code, "output": result.output[-500:]},
            )


connector = BoxSetup()
