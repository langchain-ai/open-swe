"""Version repository review settings"""

from alembic import op

revision = "c9501de19ea1"
down_revision = "4b3643ee9665"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    quoted = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '1min'")
    op.execute(
        f"ALTER TABLE {quoted}.repository ADD COLUMN IF NOT EXISTS review_settings_version uuid"
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {quoted}.version_repository_reviews() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.review_settings_version IS NOT DISTINCT FROM OLD.review_settings_version THEN
                NEW.review_settings_version := gen_random_uuid();
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute(f"""
        CREATE OR REPLACE TRIGGER version_repository_reviews
        BEFORE UPDATE OF review_enabled ON {quoted}.repository
        FOR EACH ROW EXECUTE FUNCTION {quoted}.version_repository_reviews()
    """)


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    quoted = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '1min'")
    op.execute(f"DROP TRIGGER IF EXISTS version_repository_reviews ON {quoted}.repository")
    op.execute(f"DROP FUNCTION IF EXISTS {quoted}.version_repository_reviews()")
    op.execute(f"ALTER TABLE {quoted}.repository DROP COLUMN IF EXISTS review_settings_version")
