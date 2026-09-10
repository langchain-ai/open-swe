from agent.utils.trace_metadata import searchable_trace_metadata


def test_searchable_trace_metadata_flattens_slack_run_context() -> None:
    metadata = searchable_trace_metadata(
        {
            "thread_id": "thread-1",
            "source": "slack",
            "origin": "slack",
            "thread_category": "interactive",
            "trigger_kind": "user",
            "github_login": "octocat",
            "github_user_id": "42",
            "user_email": "private@example.com",
            "repo": {"owner": "octo", "name": "repo"},
            "repo_private": True,
            "branch_name": "open-swe/task",
            "environment": "staging",
            "agent_model_id": "openai:gpt-5.4",
            "agent_effort": "high",
            "plan_mode": False,
            "slack_thread": {
                "channel_id": "C123",
                "thread_ts": "123.456",
                "triggering_event_ts": "123.457",
                "triggering_user_id": "U123",
                "triggering_user_name": "Private Name",
                "triggering_user_email": "private@example.com",
                "triggering_user_timezone": "America/New_York",
            },
        },
        {"existing": "value"},
        graph="agent",
    )

    assert metadata == {
        "existing": "value",
        "open_swe.graph": "agent",
        "open_swe.thread_id": "thread-1",
        "open_swe.source": "slack",
        "open_swe.trigger_surface": "slack",
        "open_swe.client": "slack",
        "open_swe.execution": "cloud",
        "open_swe.thread_origin": "slack",
        "open_swe.thread_origin_surface": "slack",
        "open_swe.thread_category": "interactive",
        "open_swe.trigger_kind": "user",
        "open_swe.actor_id": "slack:U123",
        "open_swe.actor_platform": "slack",
        "open_swe.actor_slack_user_id": "U123",
        "open_swe.actor_github_login": "octocat",
        "open_swe.actor_github_user_id": "42",
        "open_swe.actor_email": "private@example.com",
        "open_swe.actor_display_name": "Private Name",
        "open_swe.actor_timezone": "America/New_York",
        "open_swe.actor_account_linked": True,
        "open_swe.repo": "octo/repo",
        "open_swe.repo_owner": "octo",
        "open_swe.repo_name": "repo",
        "open_swe.repo_private": True,
        "open_swe.branch": "open-swe/task",
        "open_swe.environment": "staging",
        "open_swe.slack_channel_id": "C123",
        "open_swe.slack_thread_ts": "123.456",
        "open_swe.slack_triggering_event_ts": "123.457",
        "open_swe.agent_model": "openai:gpt-5.4",
        "open_swe.agent_effort": "high",
        "open_swe.plan_mode": False,
    }


def test_searchable_trace_metadata_distinguishes_web_desktop_and_automation() -> None:
    web = searchable_trace_metadata(
        {"source": "dashboard", "github_login": "octocat"}, graph="agent"
    )
    desktop = searchable_trace_metadata({"source": "desktop"}, graph="agent")
    desktop_cloud = searchable_trace_metadata(
        {"source": "dashboard", "client": "desktop", "execution": "cloud"}, graph="agent"
    )
    schedule = searchable_trace_metadata(
        {"source": "schedule", "schedule_id": "schedule-1", "schedule_test": True},
        graph="agent",
    )

    assert web["open_swe.trigger_surface"] == "web"
    assert web["open_swe.execution"] == "cloud"
    assert web["open_swe.actor_id"] == "github:octocat"
    assert desktop["open_swe.trigger_surface"] == "desktop"
    assert desktop["open_swe.execution"] == "local"
    assert desktop_cloud["open_swe.trigger_surface"] == "desktop"
    assert desktop_cloud["open_swe.client"] == "desktop"
    assert desktop_cloud["open_swe.execution"] == "cloud"
    assert schedule["open_swe.trigger_surface"] == "automation"
    assert schedule["open_swe.trigger_kind"] == "schedule_test"
    assert schedule["open_swe.schedule_id"] == "schedule-1"
