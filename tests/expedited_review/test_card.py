from agent.expedited_review.card import _test_diffstat
from agent.expedited_review.eligibility import ChangedFile
from agent.slack.blocks import SECTION_TEXT_MAX_CHARS


def test_long_test_paths_stay_under_the_slack_limit_and_count_the_rest() -> None:
    tests = [
        ChangedFile(filename=f"tests/{'deep/' * 28}test_{index}.py", additions=3, patch="+x")
        for index in range(20)
    ]

    [block] = _test_diffstat(tests)
    text = block["elements"][0]["text"]

    assert len(text) <= SECTION_TEXT_MAX_CHARS
    shown = text.count("test_")
    assert 0 < shown < len(tests)
    assert text.endswith(f"{len(tests) - shown} more test files on GitHub.")
