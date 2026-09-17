"""Database-operator-only, workspace-scoped cost recovery commands."""

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import text

from agent import database
from agent.database.analytics import load_workspace, workspace_id


async def operate(action: str, workspace: UUID, run: UUID | None = None) -> list[dict[str, object]]:
    if workspace != workspace_id():
        raise ValueError("Workspace does not match this deployment")
    async with database.transaction() as conn:
        if action == "requeue":
            if run is None:
                raise ValueError("Requeue requires an opaque run UUID")
            job = (
                (
                    await conn.execute(
                        text("""
                SELECT cost_event_id, invocation_id FROM run_cost_refresh
                WHERE workspace_id = :workspace AND run_id = :run AND state = 'needs_attention'
                FOR UPDATE
            """),
                        {"workspace": workspace, "run": run},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if job is None or (job["invocation_id"] is None and job["cost_event_id"] is None):
                raise ValueError("No repairable job; retained lookup context is required")
            if job["cost_event_id"] is not None:
                await conn.execute(
                    text("""
                    UPDATE outbox SET state = 'pending', attempts = 0, next_attempt_at = clock_timestamp(),
                        locked_at = NULL, dead_lettered_at = NULL, last_error = NULL
                    WHERE workspace_id = :workspace AND event_id = :event AND state = 'dead_letter'
                """),
                    {"workspace": workspace, "event": job["cost_event_id"]},
                )
            await conn.execute(
                text("""
                UPDATE run_cost_refresh SET state = :state, attempts = 0,
                    retry_started_at = clock_timestamp(), next_attempt_at = clock_timestamp(),
                    error_code = NULL, claim_token = NULL, lease_until = NULL
                WHERE workspace_id = :workspace AND run_id = :run
            """),
                {
                    "workspace": workspace,
                    "run": run,
                    "state": "awaiting_delivery" if job["cost_event_id"] else "pending",
                },
            )
        result = await conn.execute(
            text("""
            SELECT state, error_code, count(*) AS jobs, sum(attempts) AS attempts,
                count(*) FILTER (WHERE attempts > 1) AS retried_jobs,
                extract(epoch FROM clock_timestamp() - min(next_attempt_at)
                    FILTER (WHERE state IN ('pending', 'leased'))) AS oldest_due_seconds
            FROM run_cost_refresh WHERE workspace_id = :workspace GROUP BY state, error_code
            ORDER BY state, error_code
        """),
            {"workspace": workspace},
        )
        return [dict(row) for row in result.mappings()]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "requeue"))
    parser.add_argument("--workspace", required=True, type=UUID)
    parser.add_argument("--run", type=UUID)
    args = parser.parse_args()
    try:
        await load_workspace()
        print(json.dumps(await operate(args.action, args.workspace, args.run), default=str))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
