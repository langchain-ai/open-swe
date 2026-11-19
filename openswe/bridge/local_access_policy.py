"""Who may be offered approved local actions, and which bridges may back a thread.

Kept free of the agent stack (deepagents, ``openswe.tools``) because the webapp's
run-start and thread-move routes import it.
"""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import HTTPException
from runtime_api.operations import LocalReview

from openswe.bridge.store import Bridge, BridgeStore, SandboxBridgeBinding
from openswe.dashboard.settings import get_settings
from openswe.users import User

logger = logging.getLogger(__name__)


class LocalAccess:
    @classmethod
    async def offer(
        cls, requested: object, *, metadata: Mapping[str, object], login: str, owner_id: str
    ) -> str | None:
        if requested is None:
            return None
        if not isinstance(requested, str) or not requested.strip():
            raise HTTPException(422, "local_access_bridge_id must be a non-empty string")
        actor = await User.for_login("github", login)
        if not await cls.enabled_for(metadata, actor):
            raise HTTPException(409, "Cloud-first desktop access is disabled")
        owner_login = metadata.get("owner_login")
        owner = (
            await User.for_login("github", owner_login) if isinstance(owner_login, str) else None
        )
        if (
            metadata.get("visibility") != "private"
            or actor is None
            or owner is None
            or actor.id != owner.id
        ):
            raise HTTPException(403, "Local access requires its owner's private thread")
        if SandboxBridgeBinding.reaches_a_machine(metadata):
            raise HTTPException(409, "Local access requires a cloud sandbox")
        bridge = await BridgeStore.require_open(requested.strip(), owner_id=owner_id)
        if not bridge.local_access_only:
            raise HTTPException(409, "This desktop does not support approved local actions")
        return bridge.bridge_id

    @staticmethod
    async def enabled_for(metadata: Mapping[str, object], person: User | None) -> bool:
        """Cloud-first desktop is on globally or by this person's opt-in."""
        if person is not None and person.typed_preferences.experimental_cloud_first_desktop:
            return True
        settings = await get_settings()
        return settings.get("cloud_first_desktop_enabled") is True

    @classmethod
    async def offer_for_run(
        cls, requested: object, *, metadata: Mapping[str, object], login: str, owner_id: str
    ) -> str | None:
        """Offer local access to a run, or run without it when it is not available.

        A client's view of the flag, the thread or its desktop can be stale, so an
        ineligible offer drops the local tools instead of failing the message.
        Malformed requests still fail.
        """
        try:
            return await cls.offer(requested, metadata=metadata, login=login, owner_id=owner_id)
        except HTTPException as exc:
            if exc.status_code == 422:
                raise
            logger.info("Running without local access", extra={"reason": exc.detail})
            return None

    @classmethod
    async def allow_binding(cls, bridge: Bridge, metadata: Mapping[str, object]) -> None:
        if bridge.local_access_only:
            raise HTTPException(409, "An approved-actions bridge cannot be a thread sandbox")
        if bridge.client == "desktop":
            settings = await get_settings()
            if settings.get("cloud_first_desktop_enabled") is True:
                raise HTTPException(
                    409, "Cloud-first mode does not create or move This Mac threads"
                )


class LocalReviews:
    """The runtime's review of the operation running now, if it acts on the person's machine.

    Carried beside the operation's arguments, never in them: the model writes the
    arguments, and only the runtime may clear a call to run inside a grant.
    """

    _current: ContextVar[LocalReview | None] = ContextVar("local_review", default=None)

    @classmethod
    @contextmanager
    def carrying(cls, review: LocalReview | None) -> Iterator[None]:
        token = cls._current.set(review)
        try:
            yield
        finally:
            cls._current.reset(token)

    @classmethod
    def current(cls) -> LocalReview | None:
        return cls._current.get()
