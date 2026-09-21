"""Dependencies shared by the admin and public API-key routers."""

import asyncio
import logging
from typing import Any

from fastapi import Depends, HTTPException, Request

from agent.api_keys.models import ApiKey
from agent.dashboard.deps import ADMIN_DEP
from agent.database import postgres

logger = logging.getLogger(__name__)

_INVALID_KEY = "invalid API key"
# Strong references so a fire-and-forget touch is not collected mid-flight.
_TOUCHES: set[asyncio.Task[None]] = set()


def _require_database() -> None:
    if not postgres.configured():
        raise HTTPException(503, "API keys require PostgreSQL; set POSTGRES_URI")


def require_admin_with_database(admin: dict[str, Any] = ADMIN_DEP) -> dict[str, Any]:
    """The admin session, once the store the routes write to exists.

    Chained rather than declared beside the admin check so an anonymous caller
    is turned away before the deployment's storage configuration leaks.
    """
    _require_database()
    return admin


ADMIN_KEY_DEP = Depends(require_admin_with_database)


async def _touch(key_id: str) -> None:
    try:
        await ApiKey.touch(key_id)
    except Exception:
        logger.warning("Failed to record API key use", extra={"api_key_id": key_id}, exc_info=True)


def _schedule_touch(key_id: str) -> None:
    task = asyncio.create_task(_touch(key_id))
    _TOUCHES.add(task)
    task.add_done_callback(_TOUCHES.discard)


async def require_api_key(request: Request) -> ApiKey:
    _require_database()
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, _INVALID_KEY, headers={"WWW-Authenticate": "Bearer"})
    key = await ApiKey.authenticate(token)
    if key is None:
        raise HTTPException(401, _INVALID_KEY, headers={"WWW-Authenticate": "Bearer"})
    _schedule_touch(key.id)
    return key


API_KEY_DEP = Depends(require_api_key)
