"""Per-person records moved out of the LangGraph Store.

One JSON document per person, kind, and key: profiles, dashboard preferences,
custom instructions, encrypted GitHub and Notion tokens, and pending OAuth flows.
``store_import`` tracks each import out of the Store until a pass finds nothing left.
"""

from alembic import op

revision = "f11526182620"
down_revision = "e74e583ea10e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_record (
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            kind text NOT NULL,
            key text NOT NULL DEFAULT '',
            value jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (user_id, kind, key)
        )
    """)
    op.execute("""
        CREATE TABLE store_import (
            name text PRIMARY KEY,
            last_run_at timestamptz,
            moved integer NOT NULL DEFAULT 0,
            waiting integer NOT NULL DEFAULT 0,
            last_moved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            completed_at timestamptz
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
