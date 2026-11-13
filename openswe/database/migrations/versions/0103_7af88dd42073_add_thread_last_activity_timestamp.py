"""Add thread last activity timestamp"""

from alembic import op
from sqlalchemy import text

revision = "7af88dd42073"
down_revision = ["cff3e5d43f43", "147a16ec2b4a", "c04ec5f250f1"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    quoted = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    table = f"{quoted}.thread_index"
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS last_activity_at timestamptz")
    op.execute(
        f"""CREATE OR REPLACE FUNCTION {quoted}.thread_last_activity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.last_activity_at := CASE
                WHEN jsonb_typeof(NEW.metadata -> 'updated_at_ms') = 'number'
                THEN to_timestamp((NEW.metadata ->> 'updated_at_ms')::double precision / 1000)
                ELSE NEW.updated_at END;
            RETURN NEW;
        END $$"""
    )
    op.execute(f"DROP TRIGGER IF EXISTS thread_last_activity ON {table}")
    op.execute(
        f"CREATE TRIGGER thread_last_activity BEFORE INSERT OR UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {quoted}.thread_last_activity()"
    )
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("SET statement_timeout = '10min'")
        try:
            cursor: str | None = None
            while True:
                boundary = "" if cursor is None else "WHERE thread_id > :cursor"
                last = (
                    op.get_bind()
                    .execute(
                        text(
                            f"WITH batch AS MATERIALIZED (SELECT thread_id FROM {table} "
                            f"{boundary} ORDER BY thread_id LIMIT 1000), "
                            f"filled AS (UPDATE {table} AS target SET last_activity_at = NULL "
                            "FROM batch WHERE target.thread_id = batch.thread_id "
                            "AND target.last_activity_at IS NULL RETURNING target.thread_id) "
                            "SELECT max(thread_id) FROM batch"
                        ),
                        {"cursor": cursor},
                    )
                    .scalar_one()
                )
                if last is None:
                    break
                cursor = last
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {quoted}.thread_index_last_activity")
            op.execute("SET lock_timeout = '0'")
            op.execute(
                f"CREATE INDEX CONCURRENTLY thread_index_last_activity "
                f"ON {table} (last_activity_at, thread_id)"
            )
            op.execute("SET lock_timeout = '5s'")
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {quoted}.thread_index_activity")
        finally:
            op.execute("RESET lock_timeout")
            op.execute("RESET statement_timeout")


def downgrade() -> None:
    raise NotImplementedError
