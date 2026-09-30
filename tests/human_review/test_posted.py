from agent.human_review.posted import linked_pull_request


def test_a_slack_link_and_its_bare_repeat_are_one_pull_request() -> None:
    text = (
        "<https://github.com/lc/repo/pull/7|lc/repo#7> please review "
        "https://github.com/LC/repo/pull/7/files"
    )
    ref = linked_pull_request(text)
    assert ref is not None
    assert (ref.owner.lower(), ref.repo, ref.number) == ("lc", "repo", 7)


def test_a_message_linking_several_pull_requests_is_not_watched() -> None:
    text = "<https://github.com/lc/repo/pull/7> and <https://github.com/lc/repo/pull/8>"
    assert linked_pull_request(text) is None
