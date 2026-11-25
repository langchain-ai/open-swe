"""One-off copy of the SQL `skill` table into Context Hub; pause edits first."""

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from openswe.config import ENV
from openswe.database import postgres
from openswe.skill_store.store import HubSkills, SkillCreate


class SkillBackfill:
    def __init__(self, *, apply: bool = False) -> None:
        self.apply = apply

    @staticmethod
    def target() -> str:
        """The organization repository the copy writes to, refusing an implied environment.

        Repository names follow ``OPENSWE_ENV``, so run from a laptop without it, a copy
        of production's rows would land in ``robin-skills-local``.
        """
        if not (ENV.ROBIN_SKILLS_HUB_PREFIX.is_set() or ENV.OPENSWE_ENV.is_set()):
            raise SystemExit(
                "Set OPENSWE_ENV (or ROBIN_SKILLS_HUB_PREFIX) to the environment being copied"
            )
        return HubSkills.repo()

    async def migrate(self, row: Mapping[str, object]) -> None:
        value = row["value"]
        if not isinstance(value, dict):
            raise ValueError("Invalid legacy skill")
        body = SkillCreate.model_validate(
            {
                "name": row["name"],
                "description": value.get("description"),
                "instructions": value.get("instructions", ""),
            }
        )
        # Compared against Hub files, which are read back with LF line endings.
        body.instructions = body.instructions.replace("\r\n", "\n").strip()
        owner = UUID(str(row["user_id"])) if row["user_id"] is not None else None
        async with HubSkills.open(owner) as skills:
            existing = await skills.get(body.name)
            if existing is not None:
                if any(existing[key] != expected for key, expected in body.model_dump().items()):
                    raise HTTPException(409, "Destination differs; not overwriting")
            elif self.apply:
                await skills.save(body, create=True)

    async def run(self) -> int:
        after = failures = 0
        while True:
            async with postgres.session() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT id, user_id, name, value FROM skill WHERE id > :after ORDER BY id LIMIT 100"
                            ),
                            {"after": after},
                        )
                    )
                    .mappings()
                    .all()
                )
            if not rows:
                return failures
            for row in rows:
                try:
                    await self.migrate(row)
                    status = "applied-or-identical" if self.apply else "dry-run-or-identical"
                except (HTTPException, ValueError) as exc:
                    failures += 1
                    status = f"blocked: {exc}"
                print(
                    json.dumps(
                        {
                            "id": row["id"],
                            "user_id": str(row["user_id"]),
                            "name": row["name"],
                            "status": status,
                        }
                    )
                )
            after = rows[-1]["id"]

    @classmethod
    async def main(cls) -> None:
        parser = argparse.ArgumentParser(description=__doc__)
        flags = parser.add_mutually_exclusive_group()
        flags.add_argument(
            "--apply", action="store_true", help="Write to Hub; never modifies PostgreSQL"
        )
        flags.add_argument("--dry-run", action="store_true", help="Only compare (default)")
        args = parser.parse_args()
        repo = cls.target()
        print(
            json.dumps({"organization_repo": repo, "personal_repo": f"{repo}-user-<users.id>"}),
            file=sys.stderr,
        )
        backfill = cls(apply=args.apply)
        if await backfill.run():
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(SkillBackfill.main())
