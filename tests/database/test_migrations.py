from agent.database import postgres


def test_migrations_have_one_head() -> None:
    assert len(postgres.load_migrations().get_heads()) == 1
