from agent.run_config import Repo, RunConfig


def test_parse_reads_declared_and_extra_keys():
    cfg = RunConfig.parse(
        {
            "thread_id": "t1",
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "pr_number": 7,
            "breakout_from": "abc",
        }
    )
    assert cfg.thread_id == "t1"
    assert cfg.repo_full_name == "langchain-ai/open-swe"
    assert cfg.pr_number == 7
    assert cfg.get("breakout_from") == "abc"


def test_dump_round_trips_only_the_keys_that_were_set():
    raw = {"thread_id": "t1", "source": "slack", "custom": {"a": 1}}
    assert RunConfig.parse(raw).dump() == raw


def test_parse_drops_only_the_malformed_field():
    cfg = RunConfig.parse({"thread_id": "t1", "pr_number": {"not": "an int"}})
    assert cfg.thread_id == "t1"
    assert cfg.pr_number is None


def test_parse_tolerates_non_mappings():
    assert RunConfig.parse(None).dump() == {}
    assert RunConfig.parse("nope").dump() == {}


def test_parse_is_idempotent():
    cfg = RunConfig.parse({"thread_id": "t1"})
    assert RunConfig.parse(cfg) is cfg


def test_from_config_reads_the_configurable():
    assert RunConfig.from_config({"configurable": {"thread_id": "t1"}}).thread_id == "t1"
    assert RunConfig.from_config({}).thread_id is None
    assert RunConfig.from_config(None).thread_id is None


def test_nested_source_refs_are_typed():
    cfg = RunConfig.parse(
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
    assert RunConfig.parse({"eval": True}).is_eval
    assert RunConfig.parse({"reviewer_eval": True}).is_eval
    assert not RunConfig.parse({}).is_eval
