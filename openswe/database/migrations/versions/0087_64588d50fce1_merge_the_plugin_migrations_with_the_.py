"""Merge the plugin migrations with the merge queue's.

Both branch from ``a7c12a7f1462``. A merge rather than a re-chain keeps the
plugin revisions' parents as databases already stamped them, so one that ran
the plugin branch first still runs the merge queue's migration.
"""

revision = "64588d50fce1"
down_revision = ("a887e76ff00e", "cd841385552b")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError
