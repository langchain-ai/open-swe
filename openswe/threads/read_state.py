"""Each person's read position in a thread, independent of its shared metadata."""

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.database import postgres
from openswe.transcript.events import MessageCompleted
from openswe.users.records import UnknownUser, UserRecord, UserRecords, user_id_for_login
from openswe.utils.json_types import JsonObject


class ThreadReadStates(UserRecords):
    def __init__(self) -> None:
        super().__init__("thread_read_state")

    async def saved(self, login: str, thread_id: str) -> JsonObject | None:
        return await self.get(login, key=thread_id)

    async def for_threads(self, login: str, thread_ids: list[str]) -> dict[str, JsonObject]:
        return await self.for_keys(login, thread_ids)

    @staticmethod
    def response_id(saved: JsonObject) -> str | None:
        response_id = saved.get("latest_response_id")
        return response_id if isinstance(response_id, str) else None

    @classmethod
    def viewed(cls, saved: JsonObject) -> bool:
        return saved.get("viewed") is not False and (
            cls.response_id(saved) is None
            or saved.get("last_viewed_response_id") == cls.response_id(saved)
        )

    async def record_response(
        self, conn: AsyncConnection, thread_id: str, version: int, event: MessageCompleted
    ) -> None:
        """Project a completed app-request response beside its person's read cursor."""
        if event.role != "ai" or event.namespace or not event.text.strip():
            return
        sender = (
            await conn.execute(
                text(
                    """
                    SELECT sender FROM thread_message
                    WHERE thread_id = :thread_id AND role = 'human' AND version < :version
                      AND sender->>'kind' <> 'context'
                      AND NOT (
                        sender->>'kind' = 'system' AND sender->>'login' IN (
                          'system:background-task', 'system:monitor',
                          'system:event-subscription', 'system:thread-wakeup',
                          'system:workflow-push-approval', 'system:dashboard-handoff',
                          'system:sandbox-handoff', 'system:pull-request-thread',
                          'system:thread-queue', 'system:reply-guard'
                        )
                      )
                    ORDER BY (turn_id = :turn_id) DESC, version DESC
                    LIMIT 1
                    """
                ),
                {"thread_id": thread_id, "turn_id": event.turn_id, "version": version},
            )
        ).scalar_one_or_none()
        if not isinstance(sender, dict) or sender.get("kind") != "dashboard":
            return
        login = sender.get("login")
        if not isinstance(login, str):
            return
        user_id = await conn.scalar(select(user_id_for_login(login)))
        if user_id is None:
            return
        value: JsonObject = {"latest_response_id": f"message:{version}"}
        statement = insert(UserRecord).values(
            user_id=user_id, kind=self.kind, key=thread_id, value=value
        )
        await conn.execute(
            statement.on_conflict_do_update(
                index_elements=[UserRecord.user_id, UserRecord.kind, UserRecord.key],
                set_={
                    "value": UserRecord.value.op("||")(statement.excluded.value),
                    "updated_at": text("clock_timestamp()"),
                },
            )
        )

    async def set(
        self,
        login: str,
        thread_id: str,
        *,
        viewed: bool,
        run_id: str | None,
        viewed_at_ms: int,
        response_id: str | None = None,
        acknowledge_current: bool = True,
    ) -> None:
        async with postgres.transaction() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:thread_id))"),
                {"thread_id": thread_id},
            )
            user_id = await conn.scalar(select(user_id_for_login(login)))
            if user_id is None:
                raise UnknownUser(login)
            saved = (
                await conn.scalar(select(UserRecord.value).where(self._where(login, thread_id)))
                or {}
            )
            current = self.response_id(saved)
            if viewed and not acknowledge_current and response_id != current:
                return
            value: JsonObject = {
                **saved,
                "viewed": viewed,
                "last_viewed_response_id": current if viewed else None,
                "last_viewed_run_id": run_id if viewed else None,
                "last_viewed_at_ms": viewed_at_ms if viewed else None,
            }
            statement = insert(UserRecord).values(
                user_id=user_id, kind=self.kind, key=thread_id, value=value
            )
            await conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[UserRecord.user_id, UserRecord.kind, UserRecord.key],
                    set_={"value": value, "updated_at": text("clock_timestamp()")},
                )
            )


THREAD_READ_STATES = ThreadReadStates()
