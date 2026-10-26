"""The skills a run may load, by where it reads each store (``runtime_api.skills``)."""

import logging
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Final, Self

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.state import StateBackend
from runtime_api.skills import RunSkills, SkillFile

from openswe.credential_scope import private_credential_login
from openswe.sandboxes.read_only_backend import ReadOnlyBackend
from openswe.skill_store.backend import skills_backend

logger = logging.getLogger(__name__)

USER_SKILLS_ROUTE: Final = "/skills/"
ORGANIZATION_SKILLS_ROUTE: Final = "/organization-skills/"
BUNDLED_SKILLS_ROUTE: Final = "/bundled-skills/"
BUNDLED_SKILLS_DIR: Final = Path(__file__).resolve().parent.parent / "bundled_skills"
_SKILL_FILE: Final = "SKILL.md"


class RunSkillStores:
    """The bundled skills, the organization's, and the person's own on a run that acts for them.

    A later source's skill takes the place of an earlier one's of the same name.
    """

    def __init__(self, credential_login: str | None) -> None:
        self.routes: dict[str, BackendProtocol] = {
            BUNDLED_SKILLS_ROUTE: ReadOnlyBackend(
                FilesystemBackend(root_dir=BUNDLED_SKILLS_DIR, virtual_mode=True)
            ),
            ORGANIZATION_SKILLS_ROUTE: skills_backend(None),
        }
        self.sources: tuple[str, ...] = (ORGANIZATION_SKILLS_ROUTE, BUNDLED_SKILLS_ROUTE)
        if credential_login:
            self.routes[USER_SKILLS_ROUTE] = skills_backend(credential_login)
            self.sources = (USER_SKILLS_ROUTE, *self.sources)

    @classmethod
    async def for_run(cls, config: Mapping[str, object]) -> Self:
        """The stores a run's config may read: a person's own only on a private thread of theirs."""
        try:
            login = await private_credential_login(config)
        except Exception:
            logger.exception("Cannot resolve thread credential scope; omitting personal skills")
            login = None
        return cls(login)

    async def listed(self) -> RunSkills:
        """Every source's skill folders and their ``SKILL.md``."""
        files = CompositeBackend(default=ReadOnlyBackend(StateBackend()), routes=self.routes)
        folders: list[str] = []
        for source in self.sources:
            listing = await files.als(source)
            folders += [entry["path"] for entry in listing.entries or [] if entry.get("is_dir")]
        paths = [str(PurePosixPath(folder) / _SKILL_FILE) for folder in folders]
        downloaded = await files.adownload_files(paths) if paths else []
        return RunSkills(
            files=[
                SkillFile(path=file.path, content=file.content.decode())
                for file in downloaded
                if file.content is not None
            ]
        )
