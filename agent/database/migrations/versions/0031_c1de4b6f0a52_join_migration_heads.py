"""Join the two heads main grew when the API key tables landed.

``api_key`` and ``reviewer findings`` were both written against the same parent
and merged separately, so ``head`` became ambiguous and every migration refused
to run. Nothing is re-parented, because a database may already have applied
either side; this only says the two lines are one again.
"""

revision = "c1de4b6f0a52"
down_revision = ("4f14a5ad381a", "44f544a73aa3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nothing to do: the schema is whatever both sides already built."""


def downgrade() -> None:
    raise NotImplementedError
