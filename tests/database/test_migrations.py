import re

from agent.database import postgres

SEQUENTIAL_REVISIONS = {f"{n:04d}" for n in range(1, 27)}


def test_migrations_have_one_head() -> None:
    assert len(postgres.load_migrations().get_heads()) == 1


def test_new_migrations_use_random_revision_ids() -> None:
    for script in postgres.load_migrations().walk_revisions():
        if script.revision not in SEQUENTIAL_REVISIONS:
            assert re.fullmatch(r"[0-9a-f]{12}", script.revision), script.path
