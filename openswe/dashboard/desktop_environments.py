"""Alternate backends the desktop app offers to switch to until it restarts."""

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel

from openswe.config import ENV

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
    return DesktopEnvironments(environments=alternative_environments())
