"""Which of a workspace's repositories may start threads on their own.

A repository bound to a workspace is a repository the agent may work in. This
flag is the separate, narrower grant: a GitHub Actions workflow in that
repository may federate its own OIDC token into a run. It defaults to off, so
binding a repository never hands it the ability to start one.
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE workspace_repository
        ADD COLUMN may_start_threads boolean NOT NULL DEFAULT false
    """)


def downgrade() -> None:
    raise NotImplementedError
