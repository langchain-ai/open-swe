from openswe.run_config import RunConfig


def test_parse_drops_only_the_malformed_field():
    cfg = RunConfig.parse({"thread_id": "t1", "pr_number": {"not": "an int"}})
    assert cfg.thread_id == "t1"
    assert cfg.pr_number is None


def test_invocation_id_rejects_conflicting_fields():
    cfg = RunConfig.parse({"invocation_id": "inv-1", "prepare_run_id": "inv-2"})
    assert cfg.invocation_id is None
    assert cfg.prepare_run_id is None


def test_bools_are_not_accepted_as_integers():
    """Pydantic treats bool as int, which would make ``pr_number=True`` mean PR 1."""
    cfg = RunConfig.parse(
        {
            "pr_number": True,
            "chat_pr_number": False,
            "thread_id": "t1",
        }
    )
    assert cfg.pr_number is None
    assert cfg.chat_pr_number is None
    assert cfg.thread_id == "t1"


def test_dump_drops_extras_that_cannot_be_json_encoded():
    class ProxyUser:
        pass

    cfg = RunConfig.parse(
        {
            "thread_id": "t1",
            "github_login": "octocat",
            "langgraph_auth_user": ProxyUser(),
            "nested": {"user": ProxyUser()},
            "custom": {"a": 1},
        }
    )
    assert cfg.dump() == {
        "thread_id": "t1",
        "github_login": "octocat",
        "custom": {"a": 1},
    }
