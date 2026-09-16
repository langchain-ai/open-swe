"""Store the configured reasoning effort for agent runs."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run_projection ADD COLUMN configured_effort text")
    op.execute(
        """
        UPDATE run_projection AS run
        SET configured_effort = CASE model.provider_model_id
            WHEN 'openai:gpt-5.6-sol' THEN 'xhigh'
            WHEN 'openai:gpt-6-astra' THEN 'low'
            WHEN 'openai:gpt-5.6-terra' THEN 'xhigh'
            WHEN 'fireworks:accounts/fireworks/models/glm-5p3-flash' THEN 'high'
            WHEN 'anthropic:claude-opus-5' THEN 'high'
            WHEN 'openai:gpt-5.6-luna' THEN 'xhigh'
            WHEN 'google_genai:gemini-3.8-flash' THEN 'medium'
            WHEN 'anthropic:claude-sonnet-5' THEN 'high'
        END
        FROM model_directory AS model
        WHERE model.workspace_id = run.workspace_id
          AND model.model_id = run.configured_model_id
          AND model.provider_model_id IN (
              'openai:gpt-5.6-sol',
              'openai:gpt-6-astra',
              'openai:gpt-5.6-terra',
              'fireworks:accounts/fireworks/models/glm-5p3-flash',
              'anthropic:claude-opus-5',
              'openai:gpt-5.6-luna',
              'google_genai:gemini-3.8-flash',
              'anthropic:claude-sonnet-5'
          )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
