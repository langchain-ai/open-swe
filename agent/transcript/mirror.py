"""Keeping ``thread.metadata`` in step with the LangGraph metadata it mirrors.

The transcript read path authorizes a caller against its own copy of the
thread's metadata so it never has to call LangGraph. That copy is taken when
the thread is created, so every later write that changes a key the authorization
predicate reads — or the title the snapshot serves — has to be mirrored here, or
a thread flipped to private would stay readable through the transcript API.

Mirroring is best effort in the sense that it never fails the caller's own
update, but a failure is always logged: a silent drift here is a disclosure.
"""

import logging
import uuid
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue
from pydantic_core import PydanticSerializationError, to_jsonable_python

from agent.database import postgres
from agent.transcript.engine import Command, ThreadNotTranscribed, append, has_transcript
from agent.transcript.events import JsonObject, ThreadMetaPatch, ThreadMetaUpdated

logger = logging.getLogger(__name__)

MIRRORED_KEYS: frozenset[str] = frozenset(
    {
        # Read by ``thread_is_readable`` / ``thread_is_promptable`` / ``_assert_thread_postable``.
        "source",
        "visibility",
        "owner_login",
        "admin_thread",
        "thread_category",
        "schedule_id",
        "unlisted",
        # Served by the snapshot's ``ThreadView``.
        "title",
    }
)
"""The LangGraph metadata keys the transcript read path depends on."""


async def mirror_thread_metadata(thread_id: str, patch: Mapping[str, object]) -> None:
    """Mirror the authorization-relevant part of a LangGraph metadata update.

    A no-op when the update touches nothing the read path reads, when the
    thread has no transcript, or when PostgreSQL is not configured.
    """
    if not postgres.configured():
        return
    mirrored: JsonObject = {}
    for key, value in patch.items():
        if key not in MIRRORED_KEYS:
            continue
        try:
            mirrored[key] = cast(JsonValue, to_jsonable_python(value))
        except PydanticSerializationError:
            logger.warning(
                "Dropping an unserializable mirrored metadata key",
                exc_info=True,
                extra={"transcript": {"thread_id": thread_id, "metadata_key": key}},
            )
    if not mirrored:
        return
    title = mirrored.get("title")
    try:
        if not await has_transcript(thread_id):
            return
        await append(
            thread_id,
            [
                Command(
                    command_id=str(uuid.uuid7()),
                    event=ThreadMetaUpdated(
                        patch=ThreadMetaPatch(
                            title=title if isinstance(title, str) else None,
                            metadata=mirrored,
                        )
                    ),
                    actor_kind="user",
                )
            ],
        )
    except ThreadNotTranscribed:
        return
    except Exception:
        logger.warning(
            "Could not mirror thread metadata onto the transcript",
            exc_info=True,
            extra={"transcript": {"thread_id": thread_id, "keys": sorted(mirrored)}},
        )
