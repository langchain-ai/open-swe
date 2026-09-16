"""Reject opening provenance contradicted by the invocation's actual start."""

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database.postgres import close, read_only_transaction, transaction

_CANDIDATES = """
    SELECT pr.pr_id, run.run_id
    FROM pr_projection AS pr
    JOIN run_projection AS run ON run.workspace_id = pr.workspace_id
    WHERE pr.workspace_id = :workspace_id
      AND (:pr_id IS NULL OR pr.pr_id = :pr_id)
      AND (:run_id IS NULL OR run.run_id = :run_id)
      AND run.started_at > pr.opened_at + interval '24 hours'
      AND (pr.opening_run_id = run.run_id OR EXISTS (
          SELECT 1 FROM pr_run_link_projection AS link
          WHERE link.workspace_id = pr.workspace_id AND link.pr_id = pr.pr_id
            AND link.run_id = run.run_id AND link.link_role = 'opening'
      ))
"""


async def reject_false_openers(
    conn: AsyncConnection,
    *,
    workspace_id: UUID,
    pr_id: UUID | None = None,
    run_id: UUID | None = None,
    apply: bool = True,
) -> list[UUID]:
    """Clear only contradicted opening provenance, retaining outcomes and other links."""
    params = {"workspace_id": workspace_id, "pr_id": pr_id, "run_id": run_id}
    candidates = text(_CANDIDATES).bindparams(
        bindparam("pr_id", type_=Uuid), bindparam("run_id", type_=Uuid)
    )
    rows = (await conn.execute(candidates, params)).tuples().all()
    if apply:
        for affected_pr, affected_run in rows:
            identity = {
                "workspace_id": workspace_id,
                "pr_id": affected_pr,
                "run_id": affected_run,
            }
            await conn.execute(
                text(
                    "UPDATE pr_projection SET opening_run_id = NULL, originating_model_id = NULL, "
                    "model_attribution_quality = 'unavailable', updated_at = clock_timestamp() "
                    "WHERE workspace_id = :workspace_id AND pr_id = :pr_id "
                    "AND opening_run_id = :run_id"
                ),
                identity,
            )
            await conn.execute(
                text(
                    "DELETE FROM pr_run_link_projection WHERE workspace_id = :workspace_id "
                    "AND pr_id = :pr_id AND run_id = :run_id AND link_role = 'opening'"
                ),
                identity,
            )
    return sorted({UUID(str(row[0])) for row in rows}, key=str)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--pr-id", type=UUID, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Commit correction; default is read-only"
    )
    args = parser.parse_args()
    try:
        async with transaction() if args.apply else read_only_transaction() as conn:
            if args.apply:
                await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                await conn.execute(
                    text(
                        "LOCK TABLE run_projection, pr_projection, pr_run_link_projection "
                        "IN SHARE ROW EXCLUSIVE MODE"
                    )
                )
            affected = await reject_false_openers(
                conn, workspace_id=args.workspace_id, pr_id=args.pr_id, apply=args.apply
            )
        print(
            {
                "applied": args.apply,
                "workspace_id": str(args.workspace_id),
                "pr_ids": [str(pr_id) for pr_id in affected],
            }
        )
    finally:
        await close()


if __name__ == "__main__":
    asyncio.run(main())
