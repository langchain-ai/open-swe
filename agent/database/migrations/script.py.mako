<%! import json %>"""${message}"""

from alembic import op

revision = "${up_revision}"
down_revision = ${json.dumps(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    raise NotImplementedError
