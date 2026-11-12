"""Add Trace Flight seasons and best runs

``trace_flight_season`` pins the game's rules version and course seed; the open
season (no ``closed_at``) is the one runs count toward, and a rules change closes
it and opens the next. ``trace_flight_best`` keeps each person's best run in a
season with its inputs, so the game can replay it as the trace to chase. Its
score is the server's replay of those inputs, never a number the client sent.
"""

from alembic import op

revision = "147a16ec2b4a"
down_revision = ("0aae6a97dd3b", "e6cdee04906b")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS trace_flight_season (
            id uuid PRIMARY KEY,
            rules_version integer NOT NULL,
            seed bigint NOT NULL CHECK (seed BETWEEN 0 AND 4294967295),
            closed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS trace_flight_season_open"
        " ON trace_flight_season ((closed_at IS NULL)) WHERE closed_at IS NULL"
    )
    # Season 1: rules v1 on a course the scripted player clears through level 25.
    op.execute("""
        INSERT INTO trace_flight_season (id, rules_version, seed)
        SELECT '01a1c82c-98e0-7320-b60a-965dbead23be', 1, 3051132594
        WHERE NOT EXISTS (SELECT 1 FROM trace_flight_season)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS trace_flight_best (
            id uuid PRIMARY KEY,
            season_id uuid NOT NULL REFERENCES trace_flight_season (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            score integer NOT NULL,
            end_tick integer NOT NULL,
            end_reason text NOT NULL,
            flap_deltas integer[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS trace_flight_best_player"
        " ON trace_flight_best (season_id, user_id)"
    )
    # The board: highest score first, and the earlier of two equal scores ahead.
    op.execute(
        "CREATE INDEX IF NOT EXISTS trace_flight_best_board"
        " ON trace_flight_best (season_id, score DESC, updated_at)"
    )


def downgrade() -> None:
    raise NotImplementedError
