"""Workspaces, and the repository and Slack-channel bindings that route to them.

``workspace_repository`` and ``workspace_slack_channel`` key on the bound
resource, not the workspace, so the database enforces that a repository or
Slack channel belongs to at most one workspace.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace (
            id uuid PRIMARY KEY,
            slug text NOT NULL UNIQUE,
            name text NOT NULL,
            prompt text NOT NULL DEFAULT '',
            setup_script text NOT NULL DEFAULT '',
            update_script text NOT NULL DEFAULT '',
            base_snapshot_id text,
            mem_bytes bigint,
            vcpus integer,
            fs_capacity_bytes bigint,
            create_params jsonb NOT NULL DEFAULT '{}'::jsonb,
            snapshot_id text,
            snapshot_name text,
            snapshot_status text NOT NULL DEFAULT 'none' CHECK (snapshot_status IN ('none', 'capturing', 'ready', 'failed')),
            status_message text,
            snapshot_tag text,
            source_sandbox_id text,
            last_captured_at timestamptz,
            refresh_status text NOT NULL DEFAULT 'never',
            refresh_kind text,
            refresh_run_id text,
            refresh_started_at timestamptz,
            refresh_finished_at timestamptz,
            refresh_log text,
            refresh_error text,
            refresh_cron_id text,
            refresh_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
            refresh_sandbox_id text,
            created_by text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE workspace_repository (
            repository_id uuid PRIMARY KEY REFERENCES repository (id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
            linked_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX workspace_repository_workspace_idx ON workspace_repository (workspace_id)
        """
    )

    op.execute(
        """
        CREATE TABLE workspace_slack_channel (
            channel_id text PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
            linked_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX workspace_slack_channel_workspace_idx ON workspace_slack_channel (workspace_id)
        """
    )


def downgrade() -> None:
    raise NotImplementedError
