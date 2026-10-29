"""Moving a thread between its owner's machine and the cloud, checkout and all, at once."""

import logging
import uuid
from collections.abc import Mapping
from typing import Any, Self

from fastapi import HTTPException

from openswe.bridge.constants import HANDOFF_FROM_KEY
from openswe.bridge.store import Bridge, BridgeStore, SandboxBridgeBinding
from openswe.database import postgres
from openswe.dispatch import thread_workspace
from openswe.input_messages import SystemIdentity, build_input_messages
from openswe.threads.summary import TRANSCRIPT_VERSION, thread_is_owner
from openswe.transcript.engine import Command, append
from openswe.transcript.events import MessageSender, TurnCompleted, TurnRequested
from openswe.transcript.mirror import mirror_thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

SANDBOX_MOVED_KEY = "sandbox_moved"
"""Where the thread last moved, kept until the next run tells its agent."""

SANDBOX_HANDOFF_SYSTEM: SystemIdentity = {
    "id": "system:sandbox-handoff",
    "display_name": "Sandbox handoff",
    "platform": "open-swe",
}


class SandboxMove:
    """A person moving their thread onto their machine's bridge, or off it to the cloud."""

    def __init__(
        self, thread_id: str, metadata: Mapping[str, Any], update: dict[str, Any], *, login: str
    ) -> None:
        self.thread_id = thread_id
        self.update = update
        self._login = login
        self._workspace = thread_workspace(metadata)
        self._transcribed = metadata.get("transcript") == TRANSCRIPT_VERSION
        self._before = {key: metadata.get(key) for key in update}

    @classmethod
    async def requested(
        cls,
        thread_id: str,
        metadata: Mapping[str, Any],
        *,
        login: str,
        bridge: Bridge | None,
        target: object,
    ) -> Self | None:
        """The move a request asks for, or ``None`` when the thread is already there."""
        if target not in (None, "cloud"):
            raise HTTPException(422, "sandbox_target must be cloud")
        current = metadata.get("sandbox_id")
        if bridge is not None and bridge.sandbox_id != current:
            if metadata.get("visibility") != "private" or not thread_is_owner(metadata, login):
                raise HTTPException(
                    409, "only its owner's private thread can move to their machine"
                )
            update: dict[str, Any] = SandboxBridgeBinding.of(bridge).dump()
        elif bridge is None and target == "cloud" and Bridge.bridge_id_of(current) is not None:
            if not thread_is_owner(metadata, login):
                raise HTTPException(409, "only its owner can move a thread off their machine")
            update = {"sandbox_id": None, "sandbox_kind": None, "sandbox_bridge_client": None}
        else:
            return None
        if (await langgraph_client().threads.get(thread_id)).get("status") == "busy":
            raise HTTPException(409, "stop the run before moving this thread")
        # A carry an earlier move left unfinished still starts from the sandbox it left.
        source = metadata.get(HANDOFF_FROM_KEY) or current
        if (
            bridge is None
            and Bridge.bridge_id_of(source)
            and not await BridgeStore.is_connected(source)
        ):
            # A checkout no Mac is serving stays behind; the cloud starts from what was pushed.
            source = None
        update[HANDOFF_FROM_KEY] = None if source == update["sandbox_id"] else source
        update[SANDBOX_MOVED_KEY] = {
            "to_cloud": bridge is None,
            "carried": update[HANDOFF_FROM_KEY] is not None,
        }
        return cls(thread_id, metadata, update, login=login)

    @property
    def to_cloud(self) -> bool:
        return self.update["sandbox_id"] is None

    @property
    def carried(self) -> bool:
        return self.update[HANDOFF_FROM_KEY] is not None

    async def carry_out(self) -> None:
        """Point the thread at its new sandbox and carry its checkout there, or not at all.

        A carry that fails puts the thread back where it was, so it is never
        half-moved: its checkout stays in the sandbox it was asked to leave. A move to
        the cloud has no sandbox to carry it to yet: MDA creates the thread's box when
        its next run starts, and the checkout comes over when that run connects to it.
        """
        from openswe.sandboxes.lifecycle import ensure_sandbox_for_thread

        await self._write(self.update)
        if not self.to_cloud:
            try:
                await ensure_sandbox_for_thread(self.thread_id, workspace_slug=self._workspace)
            except BaseException:
                logger.warning(
                    "Could not carry a thread's checkout over; moving it back",
                    extra={"move_thread": self.thread_id},
                    exc_info=True,
                )
                await self._write(self._before)
                raise
        logger.info(
            "Moved a thread",
            extra={"move_thread": self.thread_id, "move_to_cloud": self.to_cloud},
        )
        await self._record()

    async def _write(self, metadata: dict[str, Any]) -> None:
        await langgraph_client().threads.update(thread_id=self.thread_id, metadata=metadata)
        await mirror_thread_metadata(self.thread_id, metadata)

    async def _record(self) -> None:
        """Mark the move in the thread's transcript, as a turn no run answers."""
        if not (self._transcribed and postgres.configured()):
            return
        destination = "the cloud" if self.to_cloud else f"{self._login}'s Mac"
        if self.carried and self.to_cloud:
            body = (
                "Moved this thread to the cloud. Its branch, unpushed commits and "
                "uncommitted changes come with it when its next run starts."
            )
        elif self.carried:
            body = (
                f"Moved this thread to {destination}. Its branch, unpushed commits and "
                "uncommitted changes came with it."
            )
        else:
            body = (
                f"Moved this thread to {destination}. Its Mac wasn't connected, so it "
                "continues from the branch as pushed."
            )
        [notice] = build_input_messages(
            body,
            {"sender_id": SANDBOX_HANDOFF_SYSTEM["id"], "surface": "automation", "kind": "system"},
        )
        content = notice["content"]
        text = content if isinstance(content, str) else ""
        turn_id = uuid.uuid7()
        message_id = str(uuid.uuid7())
        sender = MessageSender(
            login=self._login, kind="system", display_name=f"Moved to {destination}"
        )
        await append(
            self.thread_id,
            [
                Command(
                    command_id=f"sandbox-move:{message_id}:requested",
                    event=TurnRequested(
                        turn_id=turn_id, message_id=message_id, text=text, sender=sender
                    ),
                    actor_kind="system",
                    turn_id=turn_id,
                ),
                Command(
                    command_id=f"sandbox-move:{message_id}:completed",
                    event=TurnCompleted(turn_id=turn_id),
                    actor_kind="system",
                    turn_id=turn_id,
                ),
            ],
        )
