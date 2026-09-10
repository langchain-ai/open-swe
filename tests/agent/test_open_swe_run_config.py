from agent.run_config import OpenSWERunConfig, Repo


def test_parse_drops_only_the_malformed_field():
    cfg = OpenSWERunConfig.parse({"thread_id": "t1", "pr_number": {"not": "an int"}})
    assert cfg.thread_id == "t1"
    assert cfg.pr_number is None


def test_nested_source_refs_are_typed():
    cfg = OpenSWERunConfig.parse(
        {
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            "linear_issue": {"id": "iss"},
            "github_issue": {"number": 3},
        }
    )
    assert cfg.slack_thread is not None
    assert cfg.slack_thread.location == ("C1", "1.0")
    assert cfg.linear_issue is not None and cfg.linear_issue.id == "iss"
    assert cfg.github_issue is not None and cfg.github_issue.number == 3


def test_repo_full_name_needs_both_halves():
    assert Repo(owner="a", name="b").full_name == "a/b"
    assert Repo(owner="a").full_name == ""
    assert not Repo(owner="a")
    assert bool(Repo(owner="a", name="b"))
    assert Repo.parse(None) is None
    assert Repo.parse("a/b") is None


def test_is_eval_covers_both_flags():
    assert OpenSWERunConfig.parse({"eval": True}).is_eval
    assert OpenSWERunConfig.parse({"reviewer_eval": True}).is_eval
    assert not OpenSWERunConfig.parse({}).is_eval


def test_bools_are_not_accepted_as_integers():
    """Pydantic treats bool as int, which would make ``pr_number=True`` mean PR 1."""
    cfg = OpenSWERunConfig.parse(
        {"pr_number": True, "chat_pr_number": False, "reviewer_eval_cap": True, "thread_id": "t1"}
    )
    assert cfg.pr_number is None
    assert cfg.chat_pr_number is None
    assert cfg.reviewer_eval_cap is None
    assert cfg.thread_id == "t1"


def test_numeric_strings_still_coerce_to_int():
    assert OpenSWERunConfig.parse({"pr_number": "7"}).pr_number == 7
