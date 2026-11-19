"""Apps an instance admin adds to everyone's web UI, their permissions approved for them.

An admin features a listed app at the release they approved (``featured_apps``, an instance
setting no workspace overrides). Each person's web UI then has it, added as an install nobody
made, which follows the admin's later approvals until the person changes it, and which they
can't remove while it stays featured. Every install of it, theirs included, reads the
repositories the admin names, for each person who can read them on GitHub, and while it's
featured nobody stops sharing those. Turned off, or no longer listed by its owner, it leaves the
web UIs of the people who never changed it, and Robin stops sharing what it shared.
"""

import logging
from collections.abc import Collection
from functools import partial
from uuid import UUID

from fastapi import HTTPException

from openswe.dashboard.settings_cache import cached_instance_settings
from openswe.database import postgres
from openswe.github.http import GitHubError, GitHubSignInRequired
from openswe.plugins.records import AppVersion, Installation, PluginApps, PluginInstallations
from openswe.users import User
from openswe.utils import ttl_cache

logger = logging.getLogger(__name__)


class FeaturedApps:
    """The listed apps every person's web UI has, at the releases an admin approved."""

    # How long a person's answer for a repository stands, so someone who can't read one
    # isn't checked against GitHub on every sidebar load; access they gain shows after it.
    READ_CHECK_SECONDS = 15 * 60

    @staticmethod
    async def releases() -> list[AppVersion]:
        """Each featured app's approved release that still reads, while its owner lists it."""
        pinned = (await cached_instance_settings()).featured_app_releases
        return await PluginApps.releases(pinned, listed=True)

    @staticmethod
    async def active() -> frozenset[UUID]:
        """The ids of the apps featured now: pinned by an admin and listed by their owner,
        whether or not the approved release still reads, so an app whose release breaks
        stays in everyone's web UI as they had it."""
        pinned = (await cached_instance_settings()).featured_app_releases
        return await PluginApps.listed(list(pinned))

    @classmethod
    async def repositories(
        cls, featured: Collection[UUID] | None = None
    ) -> dict[UUID, tuple[str, ...]]:
        """The repositories each app featured now reads for everyone, by app id."""
        if featured is None:
            featured = await cls.active()
        named = (await cached_instance_settings()).featured_app_repositories
        return {app: names for app, names in named.items() if app in featured and names}

    @classmethod
    async def shares(cls, app: UUID, repository: str) -> bool:
        """Whether an admin has the app, featured now, read ``repository`` for everyone."""
        return repository.lower() in (await cls.repositories()).get(app, ())

    @classmethod
    async def add_for(cls, person: User) -> tuple[frozenset[UUID], list[Installation]]:
        """Brings the person's web UI in line with what's featured.

        It takes back each app no longer featured that they never changed, first, so a
        newly featured app of the same name takes its place; adds each featured app, as
        ``PluginInstallations.feature`` does; stops sharing what Robin shared and no admin
        names any longer; and shares with every install of a featured app the repositories
        an admin names that the person can read.
        """
        settings = await cached_instance_settings()
        pinned = settings.featured_app_releases
        async with postgres.session() as session:
            featured = await PluginApps.listed(list(pinned), session=session)
            if not featured and not await PluginInstallations.robin_added(
                person.id, session=session
            ):
                return featured, await PluginInstallations.visible_to(person.id, session=session)
            versions = await PluginApps.releases(pinned, listed=True, session=session)
            named = {
                app: names
                for app, names in settings.featured_app_repositories.items()
                if app in featured and names
            }
            await PluginInstallations.retract(person.id, featured, session=session)
            await PluginInstallations.feature(person.id, versions, session=session)
            await PluginInstallations.unshare_unfeatured(person.id, named, session=session)
            installations = await PluginInstallations.visible_to(person.id, session=session)
        shares: list[tuple[UUID, list[str]]] = []
        for installation in installations:
            if names := named.get(installation.plugin):
                shares.append((installation.id, await cls._readable(person, installation, names)))
        if any(names for _, names in shares):
            async with postgres.session() as session:
                for installation_id, names in shares:
                    await PluginInstallations.share(
                        installation_id, person.id, names, added_by_robin=True, session=session
                    )
                installations = await PluginInstallations.visible_to(person.id, session=session)
        return featured, installations

    @staticmethod
    async def _readable(
        person: User, installation: Installation, names: tuple[str, ...]
    ) -> list[str]:
        readable: list[str] = []
        for name in names:
            if name in installation.repositories:
                continue
            try:
                if await ttl_cache.cached(
                    f"featured-repository-read:{person.id}:{name}",
                    FeaturedApps.READ_CHECK_SECONDS,
                    partial(person.can_read_repository, name),
                    serve_stale=False,
                ):
                    readable.append(name)
            except HTTPException, GitHubError, GitHubSignInRequired:
                # Shared on a later load, once GitHub answers.
                logger.warning(
                    "Could not check a featured app's repository",
                    extra={"repository": name, "user_id": str(person.id)},
                    exc_info=True,
                )
        return readable
