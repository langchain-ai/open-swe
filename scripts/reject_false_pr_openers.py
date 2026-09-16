"""Reject opening provenance contradicted by the invocation's actual start."""

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import text

from agent.analytics.ingestion import reject_false_openers
from agent.database.postgres import close, read_only_transaction, transaction


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
