"""Build analytics indexes online, outside the schema migration transaction."""

import asyncio
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from agent.database import postgres


@dataclass(frozen=True)
class _Relation:
    oid: int
    schema: str
    name: str
    parent_oid: int | None
    partitioned: bool


@dataclass(frozen=True)
class _Index:
    oid: int
    schema: str
    name: str
    table_oid: int
    parent_oid: int | None
    valid: bool


_INDEX_QUERY = """
    SELECT c.oid, n.nspname AS schema, c.relname AS name, i.indrelid AS table_oid,
        h.inhparent AS parent_oid, i.indisvalid AS valid
    FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_inherits h ON h.inhrelid = c.oid
"""


def _qualified(conn: AsyncConnection, schema: str, name: str) -> str:
    quote = conn.dialect.identifier_preparer.quote_identifier
    return f"{quote(schema)}.{quote(name)}"


async def _index(conn: AsyncConnection, schema: str, name: str) -> _Index | None:
    row = (
        (
            await conn.execute(
                text(_INDEX_QUERY + " WHERE n.nspname = :schema AND c.relname = :name"),
                {"schema": schema, "name": name},
            )
        )
        .mappings()
        .one_or_none()
    )
    return _Index(**row) if row is not None else None


async def _metadata(conn: AsyncConnection, statement: str) -> None:
    # These operations only change catalogs; do not queue behind long-running writers.
    await conn.execute(text("SET lock_timeout = '2s'"))
    await conn.execute(text(statement))
    await conn.execute(text("RESET lock_timeout"))


async def _ensure_index(
    conn: AsyncConnection, relation: _Relation, name: str, columns: str, predicate: str = ""
) -> _Index:
    existing = await _index(conn, relation.schema, name)
    qualified = _qualified(conn, relation.schema, name)
    if existing is not None:
        if existing.table_oid != relation.oid:
            raise RuntimeError(f"analytics index belongs to another table: {qualified}")
        if existing.valid or relation.partitioned:
            return existing
        if existing.parent_oid is not None:
            await conn.execute(text(f"REINDEX INDEX CONCURRENTLY {qualified}"))
            repaired = await _index(conn, relation.schema, name)
            assert repaired is not None and repaired.valid
            return repaired
        await conn.execute(text(f"DROP INDEX CONCURRENTLY {qualified}"))
    table = _qualified(conn, relation.schema, relation.name)
    index_name = conn.dialect.identifier_preparer.quote_identifier(name)
    if relation.partitioned:
        await _metadata(conn, f"CREATE INDEX {index_name} ON ONLY {table} ({columns})")
    else:
        await conn.execute(
            text(f"CREATE INDEX CONCURRENTLY {index_name} ON {table} ({columns}){predicate}")
        )
    created = await _index(conn, relation.schema, name)
    assert created is not None
    return created


async def _partitioned_index(
    conn: AsyncConnection, relations: list[_Relation], name: str, columns: str
) -> None:
    indexes: dict[int, _Index] = {}
    for relation in relations:
        attached: _Index | None = None
        if relation.parent_oid is not None:
            row = (
                (
                    await conn.execute(
                        text(
                            _INDEX_QUERY
                            + " WHERE i.indrelid = :table_oid AND h.inhparent = :parent_oid"
                        ),
                        {"table_oid": relation.oid, "parent_oid": indexes[relation.parent_oid].oid},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                attached = _Index(**row)
        index_name = (
            attached.name
            if attached
            else (name if relation.parent_oid is None else f"{name}_{relation.oid}")
        )
        indexes[relation.oid] = await _ensure_index(conn, relation, index_name, columns)
    for relation in reversed(relations):
        if relation.parent_oid is None:
            continue
        child = indexes[relation.oid]
        parent = indexes[relation.parent_oid]
        if child.parent_oid == parent.oid:
            continue
        await _metadata(
            conn,
            f"ALTER INDEX {_qualified(conn, parent.schema, parent.name)} "
            f"ATTACH PARTITION {_qualified(conn, child.schema, child.name)}",
        )
    valid = await conn.scalar(
        text("SELECT indisvalid FROM pg_index WHERE indexrelid = :oid"),
        {"oid": indexes[relations[0].oid].oid},
    )
    if not valid:
        raise RuntimeError(f"analytics partitioned index remains invalid: {name}")


async def ensure_indexes() -> None:
    """Serialize online builds on a dedicated connection that cannot leak a session lock."""
    engine = create_async_engine(
        postgres.engine().url,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"server_settings": {"application_name": "open-swe-analytics-indexes"}},
    )
    try:
        async with engine.connect() as conn:
            try:
                # A blocking advisory-lock SELECT holds a snapshot that can stall the builder.
                while not await conn.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:scope, 0))"),
                    {"scope": f"analytics-indexes:{postgres.SCHEMA}"},
                ):
                    await asyncio.sleep(0.25)
                outbox_oid = await conn.scalar(
                    text("SELECT CAST(:table AS regclass)::oid"),
                    {"table": _qualified(conn, postgres.SCHEMA, "outbox")},
                )
                assert isinstance(outbox_oid, int)
                await _ensure_index(
                    conn,
                    _Relation(outbox_oid, postgres.SCHEMA, "outbox", None, False),
                    "outbox_acknowledged_at_idx",
                    "acknowledged_at",
                    " WHERE state = 'acknowledged'",
                )
                rows = (
                    (
                        await conn.execute(
                            text("""
                    SELECT c.oid, n.nspname AS schema, c.relname AS name,
                        tree.parentrelid::oid AS parent_oid, c.relkind = 'p' AS partitioned
                    FROM pg_partition_tree(CAST(:table AS regclass)) tree
                    JOIN pg_class c ON c.oid = tree.relid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    ORDER BY tree.level, c.oid
                """),
                            {"table": _qualified(conn, postgres.SCHEMA, "events")},
                        )
                    )
                    .mappings()
                    .all()
                )
                relations = [_Relation(**row) for row in rows]
                await _partitioned_index(conn, relations, "events_occurred_at_idx", "occurred_at")
                await _partitioned_index(
                    conn,
                    relations,
                    "events_workspace_recorded_idx",
                    "workspace_id, recorded_at DESC",
                )
            finally:
                await conn.invalidate()
    finally:
        await engine.dispose()
