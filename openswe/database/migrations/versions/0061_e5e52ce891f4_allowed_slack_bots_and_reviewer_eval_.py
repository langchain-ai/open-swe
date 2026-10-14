"""Allowed Slack bots and reviewer eval runs moved out of the LangGraph Store.

``allowed_slack_bot`` keys on the Slack workspace and bot it allows.
``reviewer_eval_run`` keeps one row per launched eval; the dashboard shows the
latest. Its sandbox reports progress with a per-run token whose SHA-256 digest
is ``report_token_hash``. In both, ``copied_from_store`` marks a row the Store
import wrote and nothing has written since.
"""

from alembic import op

revision = "e5e52ce891f4"
down_revision = ["2cd871a2607d", "fcab215aad3e", "bdc6cc9ba2a9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE allowed_slack_bot (
            team_id text NOT NULL,
            bot_id text NOT NULL,
            user_id text NOT NULL DEFAULT '',
            app_id text NOT NULL DEFAULT '',
            name text NOT NULL,
            image_url text NOT NULL DEFAULT '',
            created_by text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            copied_from_store boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (team_id, bot_id)
        )
    """)
    op.execute("""
        CREATE TABLE reviewer_eval_run (
            id uuid PRIMARY KEY,
            status text NOT NULL
                CHECK (status IN ('starting', 'running', 'completed', 'failed')),
            config jsonb NOT NULL,
            example_limit integer,
            created_by text,
            sandbox_name text,
            report_token_hash text UNIQUE,
            experiment_url text,
            error text,
            log_tail text,
            completed_examples integer,
            total_examples integer,
            started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            heartbeat_at timestamptz,
            finished_at timestamptz,
            copied_from_store boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX reviewer_eval_run_started_at ON reviewer_eval_run (started_at)")


def downgrade() -> None:
    raise NotImplementedError
