"""Allowed Slack bots and reviewer eval runs moved out of the LangGraph Store.

``allowed_slack_bot`` holds one row per Slack workspace and bot it allows.
``reviewer_eval_run`` keeps one row per launched eval, created when it
launches; the dashboard shows the latest. Its sandbox reports progress with a
per-run token whose SHA-256 digest is ``report_token_hash``. In both,
``copied_from_store`` marks a row the Store import wrote and nothing has
written since.
"""

from alembic import op

revision = "e5e52ce891f4"
down_revision = "0d10512c3176"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE allowed_slack_bot (
            id uuid PRIMARY KEY,
            team_id text NOT NULL,
            bot_id text NOT NULL,
            user_id text NOT NULL DEFAULT '',
            app_id text NOT NULL DEFAULT '',
            name text NOT NULL,
            image_url text NOT NULL DEFAULT '',
            copied_from_store boolean NOT NULL DEFAULT false,
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX allowed_slack_bot_team_bot ON allowed_slack_bot (team_id, bot_id)"
    )
    op.execute("""
        CREATE TABLE reviewer_eval_run (
            id uuid PRIMARY KEY,
            status text NOT NULL
                CHECK (status IN ('starting', 'running', 'completed', 'failed')),
            config jsonb NOT NULL,
            example_limit integer,
            sandbox_name text,
            report_token_hash text UNIQUE,
            experiment_url text,
            error text,
            log_tail text,
            completed_examples integer,
            total_examples integer,
            heartbeat_at timestamptz,
            finished_at timestamptz,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX reviewer_eval_run_created_at ON reviewer_eval_run (created_at)")


def downgrade() -> None:
    raise NotImplementedError
