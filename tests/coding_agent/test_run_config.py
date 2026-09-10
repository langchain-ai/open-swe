from types import SimpleNamespace
from typing import Any, cast

from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.run_config import OpenSWERunConfig
from coding_agent.run_config import RunConfig

PLATFORM_CONFIGURABLE: dict[str, Any] = {
    "thread_id": "t1",
    "run_id": "r1",
    "source": "slack",
    "agent_model_id": "anthropic:claude",
    "agent_effort": "medium",
    "plan_mode": True,
    "eval": False,
    "watch_key": "w1",
    "github_login": "ramonn",
    "user_email": "ramon@example.com",
    "repo": {"owner": "langchain-ai", "name": "open-swe"},
    "branch_name": "main",
    "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
    "linear_issue": {"id": "iss"},
    "github_issue": {"number": 3},
    "pr_number": 7,
    "head_sha": "abc",
    "reviewer_event": "opened",
    "draft_prs": True,
    "chat_pr_number": 9,
    "review_style_top_reviewers": ["a", "b"],
    "reviewer_eval": True,
    "automation_slack_notification": {"channel_id": "C2", "mode": "always"},
    "breakout_from": "abc",
}


def test_parse_reads_declared_and_extra_keys():
    cfg = RunConfig.parse({"thread_id": "t1", "watch_key": "w1", "breakout_from": "abc"})
    assert cfg.thread_id == "t1"
    assert cfg.watch_key == "w1"
    assert cfg.get("breakout_from") == "abc"


def test_dump_round_trips_only_the_keys_that_were_set():
    raw = {"thread_id": "t1", "source": "slack", "custom": {"a": 1}}
    assert RunConfig.parse(raw).dump() == raw


def test_platform_keys_survive_the_generic_class():
    """A generic reader must not cost a run the keys it does not know about."""
    generic = RunConfig.parse(PLATFORM_CONFIGURABLE).dump()
    assert generic == PLATFORM_CONFIGURABLE
    assert generic == OpenSWERunConfig.parse(PLATFORM_CONFIGURABLE).dump()


def test_platform_fields_are_typed_on_the_platform_class():
    cfg = OpenSWERunConfig.parse(PLATFORM_CONFIGURABLE)
    assert cfg.slack_thread is not None
    assert cfg.slack_thread.location == ("C1", "1.0")
    assert cfg.repo_full_name == "langchain-ai/open-swe"
    assert cfg.pr_number == 7
    assert cfg.automation_slack_notification is not None
    assert cfg.automation_slack_notification.channel_id == "C2"
    assert cfg.thread_id == "t1"


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


def test_is_eval_reads_the_generic_flag():
    assert RunConfig.parse({"eval": True}).is_eval
    assert not RunConfig.parse({"reviewer_eval": True}).is_eval
    assert OpenSWERunConfig.parse({"reviewer_eval": True}).is_eval


def _request(runtime: Any) -> ToolCallRequest:
    return cast(ToolCallRequest, SimpleNamespace(runtime=runtime))


def test_from_tool_request_reads_the_runtime_config():
    request = _request(SimpleNamespace(config={"configurable": {"thread_id": "t1"}}))
    assert RunConfig.from_tool_request(request).thread_id == "t1"


def test_from_tool_request_without_a_config_is_empty():
    assert RunConfig.from_tool_request(_request(None)).dump() == {}
