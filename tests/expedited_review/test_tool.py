from agent.tools.expedite_pr_approval import _draft_next_step


def test_draft_next_step_requires_a_slack_thread_reply() -> None:
    pr_url = "https://github.com/lc/repo/pull/7"

    next_step = _draft_next_step(pr_url)

    assert "Nothing was posted in the Slack thread" in next_step
    assert "slack_reply" in next_step
    assert pr_url in next_step
    assert "mark the PR ready from the DM card" in next_step
    assert "/baby-sit" in next_step
