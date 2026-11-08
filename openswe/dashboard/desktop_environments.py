"""What the desktop app learns from the backend before signing in.

The alternate backends it offers to switch to until it restarts, and the origins
that serve this dashboard, whose links it opens as its own.
"""

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel

from openswe.config import ENV
from openswe.utils.dashboard_links import PublicOrigin

logger = logging.getLogger(__name__)

router = APIRouter()


class DesktopEnvironment(BaseModel):
    name: str
    url: str

    @classmethod
    def parse(cls, entry: str) -> DesktopEnvironment | None:
        """Read one ``name:url`` entry; the URL keeps its own colons."""
        name, _, url = (part.strip() for part in entry.partition(":"))
        target = urlsplit(url)
        if not name or target.scheme not in ("http", "https") or not target.netloc:
            return None
        return cls(name=name, url=url)


class DesktopEnvironments(BaseModel):
    environments: list[DesktopEnvironment]
    # The dashboard's own origin first, then its aliases (``DASHBOARD_ALIAS_URLS``).
    dashboard_origins: list[str]


def alternative_environments() -> list[DesktopEnvironment]:
    environments: list[DesktopEnvironment] = []
    for entry in ENV.OPENSWE_ALTERNATIVE_ENVS.get_list():
        environment = DesktopEnvironment.parse(entry)
        if environment is None:
            logger.warning(
                "Ignoring malformed OPENSWE_ALTERNATIVE_ENVS entry", extra={"entry": entry}
            )
            continue
        environments.append(environment)
    return environments


@router.get("/desktop/environments")
async def api_desktop_environments() -> DesktopEnvironments:
    return DesktopEnvironments(
        environments=alternative_environments(), dashboard_origins=PublicOrigin.all()
    )
