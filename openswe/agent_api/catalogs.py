"""The catalogs MDA runs start with, kept by content for the run tokens that name them.

The backend works out a run's catalog once, as it starts the run, and keeps it
here; the run's token signs its digest, and the run reads it with ``CATALOG``.
It never rides the run's context: the Agent Server copies that into the run's
config and its thread's, and returns both on every read of the run or thread.
"""

import hashlib
import json
from datetime import timedelta
from typing import Final
from uuid import uuid7

from runtime_api.catalog import Catalog
from sqlalchemy import text

from openswe.database.postgres import transaction

# Past the life of any run token, so no run outlives the catalog it names.
KEPT_FOR: Final = timedelta(days=3)
PRUNE_JOB: Final = "run_catalog_prune"
PRUNE_SCHEDULE: Final = "23 * * * *"


class CatalogNotKeptError(LookupError):
    """No catalog is kept under a digest a valid run token names."""


async def keep(catalog: Catalog) -> str:
    """Keep ``catalog`` and return the digest its runs name it by.

    It grants nothing: each run's grants are its own, added as the run reads it.
    """
    body = json.dumps(catalog.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(body.encode()).hexdigest()
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO run_catalog (id, digest, catalog) "
                "VALUES (:id, :digest, CAST(:catalog AS jsonb)) "
                "ON CONFLICT (digest) DO UPDATE SET updated_at = clock_timestamp()"
            ),
            {"id": uuid7(), "digest": digest, "catalog": body},
        )
    return digest


async def kept(digest: str) -> Catalog:
    async with transaction() as conn:
        body = (
            await conn.execute(
                text("SELECT catalog::text FROM run_catalog WHERE digest = :digest"),
                {"digest": digest},
            )
        ).scalar_one_or_none()
    if body is None:
        raise CatalogNotKeptError(digest)
    return Catalog.model_validate_json(body)


async def prune() -> int:
    """Forget the catalogs no run has started with for longer than any run token lives."""
    async with transaction() as conn:
        result = await conn.execute(
            text("DELETE FROM run_catalog WHERE updated_at < clock_timestamp() - :age"),
            {"age": KEPT_FOR},
        )
    return result.rowcount
