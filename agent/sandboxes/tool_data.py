"""Postgres records supporting sandbox tool access."""

import json

from pydantic import BaseModel, JsonValue
from sqlalchemy import text

from agent import database


class ToolContext(BaseModel):
    configurable: dict[str, JsonValue]


async def save_context(thread_id: str, context: ToolContext) -> None:
    async with database.transaction() as conn:
        await conn.execute(
            text("""
            INSERT INTO sandbox_tool_context (thread_id, configurable)
            VALUES (:thread_id, CAST(:configurable AS jsonb))
            ON CONFLICT (thread_id) DO UPDATE SET configurable = excluded.configurable
        """),
            {"thread_id": thread_id, "configurable": json.dumps(context.configurable)},
        )


async def load_context(thread_id: str) -> ToolContext | None:
    async with database.connection() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT configurable FROM sandbox_tool_context WHERE thread_id = :thread_id
        """),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    return ToolContext.model_validate(row) if row is not None else None
