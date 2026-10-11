"""Remove personal model selection defaults"""

from alembic import op

revision = "da651a8f67cc"
down_revision = ["4ce55eec2786", "6f06bbadfc02", "a823229a905f", "1b0248b89a95", "f11526182620"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE user_record
        SET value = value - ARRAY[
            'default_model', 'reasoning_effort', 'default_subagent_model',
            'subagent_reasoning_effort', 'model_routing_enabled'
        ]::text[]
        WHERE kind = 'profile'
        """
    )


def downgrade() -> None:
    raise NotImplementedError
