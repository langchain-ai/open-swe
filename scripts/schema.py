"""Generate the readable database reference from applied migrations."""

import asyncio
import sys
from pathlib import Path

from sqlacodegen.generators import TablesGenerator
from sqlalchemy import MetaData

from openswe.database import postgres

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "openswe/database/schema.py"


async def main() -> None:
    await postgres.migrate()
    try:
        async with postgres.connection() as connection:
            metadata = MetaData(schema=postgres.SCHEMA)
            await connection.run_sync(
                lambda conn: metadata.reflect(bind=conn, schema=postgres.SCHEMA)
            )
            metadata.remove(metadata.tables[f"{postgres.SCHEMA}.alembic_version"])
            source = await connection.run_sync(
                lambda conn: TablesGenerator(metadata, conn, options=[]).generate()
            )
        process = await asyncio.create_subprocess_exec(
            "ruff",
            "format",
            "--stdin-filename",
            str(SCHEMA_FILE),
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        formatted, _ = await process.communicate(source.encode())
        if process.returncode != 0:
            raise RuntimeError("Formatting the schema snapshot failed")
        if "--check" in sys.argv:
            if not SCHEMA_FILE.exists() or SCHEMA_FILE.read_bytes() != formatted:
                sys.exit("Schema snapshot is stale. Run make schema and commit the result.")
        else:
            SCHEMA_FILE.write_bytes(formatted)
    finally:
        await postgres.close()


asyncio.run(main())
