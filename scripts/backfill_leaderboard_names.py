"""Backfill missing leaderboard names: uv run python -m scripts.backfill_leaderboard_names [--apply]."""

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import text

from agent.database import close, connection, transaction
from agent.database.analytics import load_workspace, workspace_id

_ELIGIBLE = """
    workspace_id = :workspace_id
    AND NULLIF(btrim(github_login), '') IS NOT NULL
    AND NULLIF(btrim(display_name), '') IS NULL
    AND anonymize_after > clock_timestamp()
"""


async def github_name(login: str) -> str | None:
    """Read a public profile using the operator's authenticated GitHub CLI."""
    process = await asyncio.create_subprocess_exec(
        "gh",
        "api",
        f"users/{login}",
        "--jq",
        ".name",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"GitHub profile lookup failed for {login}: {stderr.decode().strip()}")
    name = stdout.decode().strip()
    return name if name and name != "null" else None


async def backfill(*, apply: bool = False) -> int:
    """Fill blank names without extending retention or overwriting concurrent updates."""
    workspace = workspace_id()
    async with connection() as conn:
        result = await conn.execute(
            text(
                f"SELECT person_id, github_login FROM identity_directory WHERE {_ELIGIBLE} ORDER BY person_id"
            ),
            {"workspace_id": workspace},
        )
        candidates: list[tuple[UUID, str]] = [(row.person_id, row.github_login) for row in result]
    count = 0
    for person_id, login in candidates:
        name = await github_name(login)
        if name is None:
            continue
        if apply:
            async with transaction() as conn:
                updated = await conn.execute(
                    text(f"""
                        UPDATE identity_directory
                        SET display_name = :name, updated_at = clock_timestamp()
                        WHERE {_ELIGIBLE} AND person_id = :person_id AND github_login = :login
                        RETURNING person_id
                    """),
                    {
                        "workspace_id": workspace,
                        "person_id": person_id,
                        "login": login,
                        "name": name,
                    },
                )
                if updated.scalar_one_or_none() is None:
                    continue
        print(json.dumps({"login": login, "name": name, "applied": apply}))
        count += 1
    return count


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill blank analytics display names in the deployment selected by POSTGRES_URI. "
        "Requires authenticated gh. Dry-run by default; does not migrate the database. "
        "Existing names and expired identities are preserved. Failures stop the run; rerunning is safe."
    )
    parser.add_argument("--apply", action="store_true", help="Write names (default: preview only)")
    args = parser.parse_args()
    try:
        await load_workspace()
        count = await backfill(apply=args.apply)
        print(f"{'Updated' if args.apply else 'Would update'} {count} identities")
    finally:
        await close()


if __name__ == "__main__":
    asyncio.run(main())
