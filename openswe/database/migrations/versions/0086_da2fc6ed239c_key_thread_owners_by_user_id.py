"""Key thread owners by user id.

A thread named its owner by GitHub login, which GitHub gives to someone else
after a rename. Each thread now also records ``owner_user_id``, and ownership
is checked against it. Existing threads take the account that holds their
owner's login today: a login is held by one account since the previous
migration, and there is nothing older to go on. A review chat names its
person ``github_login``.
"""

from alembic import op

revision = "da2fc6ed239c"
down_revision = "43aa7818561c"
branch_labels = None
depends_on = None

_OWNER_LOGIN = """lower(coalesce(
    nullif(metadata->>'owner_login', ''),
    CASE WHEN metadata->>'source' = 'review_chat' THEN metadata->>'github_login' END
))"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in ("thread_index", "thread"):
        op.execute(f"""
            UPDATE {table}
            SET metadata = metadata || jsonb_build_object('owner_user_id', owner.user_id::text)
            FROM user_identity AS owner
            WHERE owner.provider = 'github'
                AND owner.login <> ''
                AND lower(owner.login) = {_OWNER_LOGIN}
                AND metadata->>'owner_user_id' IS NULL
        """)


def downgrade() -> None:
    raise NotImplementedError
