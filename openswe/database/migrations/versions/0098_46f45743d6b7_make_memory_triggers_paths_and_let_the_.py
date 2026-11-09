"""Make memory triggers paths and let the curator write guidance at them.

``recall(path)`` matches a trigger at the path or above it, splitting on ``/``, so older
slugs such as ``repo:org/name`` become ``repo/org/name``. Where that collides with a
trigger already named so, the two merge: links move to the one kept, and the other goes.
"""

from alembic import op

revision = "46f45743d6b7"
down_revision = ["bafee27672d1", "71279468cbb4", "4a112f9ccfc9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE memory_trigger ADD COLUMN guidance text NOT NULL DEFAULT ''")
    op.execute(
        "COMMENT ON COLUMN memory_trigger.guidance IS 'What the workspace''s curator "
        "wrote at this path for every thread of the workspace; empty for none.'"
    )
    # Per workspace and path, keep a trigger already named with `/`, else the oldest.
    op.execute("""
        CREATE TEMPORARY TABLE memory_trigger_path ON COMMIT DROP AS
        SELECT id, rtrim(replace(slug, ':', '/'), '/') AS path, first_value(id) OVER (
            PARTITION BY workspace_id, rtrim(replace(slug, ':', '/'), '/')
            ORDER BY slug LIKE '%:%', created_at, id
        ) AS keep
        FROM memory_trigger
    """)
    op.execute("""
        UPDATE memory_trigger_link AS link
        SET trigger_id = merged.keep, updated_at = clock_timestamp()
        FROM memory_trigger_path AS merged
        WHERE link.trigger_id = merged.id
          AND merged.id <> merged.keep
          AND NOT EXISTS (
              SELECT 1 FROM memory_trigger_link AS kept
              WHERE kept.trigger_id = merged.keep AND kept.memory_id = link.memory_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM memory_trigger_link AS twin
              JOIN memory_trigger_path AS twin_path ON twin_path.id = twin.trigger_id
              WHERE twin_path.keep = merged.keep
                AND twin_path.id <> twin_path.keep
                AND twin.memory_id = link.memory_id
                AND twin.id < link.id
          )
    """)
    op.execute("""
        DELETE FROM memory_trigger AS named
        USING memory_trigger_path AS merged
        WHERE named.id = merged.id AND merged.id <> merged.keep
    """)
    op.execute("""
        UPDATE memory_trigger AS named
        SET slug = merged.path, updated_at = clock_timestamp()
        FROM memory_trigger_path AS merged
        WHERE named.id = merged.id AND named.slug <> merged.path
    """)


def downgrade() -> None:
    raise NotImplementedError
