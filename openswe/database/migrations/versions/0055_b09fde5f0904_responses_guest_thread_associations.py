"""Responses guest thread associations"""

from alembic import op

revision = "b09fde5f0904"
down_revision = ["6f06bbadfc02", "e74e583ea10e"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE responses_guest_thread (
            thread_id TEXT PRIMARY KEY,
            host_thread_id TEXT NOT NULL
        )
    """)
    op.create_index("ix_responses_guest_thread_host", "responses_guest_thread", ["host_thread_id"])


def downgrade() -> None:
    raise NotImplementedError
