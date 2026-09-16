"""The repositories a thread works in, linked from thread metadata by id.

``metadata["repository_ids"]`` is the record: ``repository.id`` values, as
strings, in the order the repositories were added. ``repo_owner``/``repo_name``
and ``repo`` are the shapes older threads were written with; readers resolve
them to a :class:`Repository` row and writers never produce them.
"""

import logging
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from agent.github.repositories import Repository
from agent.run_config import Repo

logger = logging.getLogger(__name__)

REPOSITORY_IDS_METADATA_KEY = "repository_ids"
REPO_EXPLICITLY_NONE_METADATA_KEY = "repo_explicitly_none"


def parse_repository_ids(raw: object) -> list[UUID]:
    """Well-formed, distinct ids from a ``repository_ids``-shaped list, in order."""
    if not isinstance(raw, list):
        return []
    ids: list[UUID] = []
    for item in raw:
        try:
            parsed = UUID(str(item))
        except ValueError:
            logger.warning("Ignoring malformed repository id", extra={"repository_id": item})
            continue
        if parsed not in ids:
            ids.append(parsed)
    return ids


def thread_repository_ids(metadata: Mapping[str, Any]) -> list[UUID]:
    return parse_repository_ids(metadata.get(REPOSITORY_IDS_METADATA_KEY))


def legacy_repo(metadata: Mapping[str, Any]) -> Repo | None:
    """The repository an older thread named inline, before ``repository_ids``."""
    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return Repo(owner=owner, name=name)
    return Repo.parse(metadata.get("repo"))


async def thread_repositories(metadata: Mapping[str, Any]) -> list[Repository]:
    """Every repository the thread works in, in the order they were added."""
    ids = thread_repository_ids(metadata)
    if ids:
        return await Repository.get_many(ids)
    legacy = legacy_repo(metadata)
    return [await Repository.ensure(legacy.full_name)] if legacy else []


def repository_ids_metadata(repositories: Iterable[Repository]) -> list[str]:
    """The JSON value to store under ``metadata["repository_ids"]``."""
    ids: list[str] = []
    for repository in repositories:
        value = str(repository.id)
        if value not in ids:
            ids.append(value)
    return ids
