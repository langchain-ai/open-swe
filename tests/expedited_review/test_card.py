from unittest.mock import AsyncMock

import pytest

from openswe.expedited_review.card import _diffstat, open_card
from openswe.expedited_review.eligibility import ChangedFile, Exclusion
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.blocks import SECTION_TEXT_MAX_CHARS


def test_long_test_paths_stay_under_the_slack_limit_and_count_the_rest() -> None:
    tests = [
        ChangedFile(filename=f"tests/{'deep/' * 28}test_{index}.py", additions=3, patch="+x")
        for index in range(20)
    ]

    [block] = _diffstat("Tests", "test files", tests)
    text = block["elements"][0]["text"]

    assert len(text) <= SECTION_TEXT_MAX_CHARS
    shown = text.count("test_")
    assert 0 < shown < len(tests)
    assert text.endswith(f"{len(tests) - shown} more test files on GitHub.")


async def test_configured_channel_hides_send_controls(
    monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    approval = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        slack_channel_choices=[{"id": "C1", "name": "kitchen"}],
    )
    approval.pull_request = pr
    settings = RepoSettings(review_channel="C2")
    monkeypatch.setattr(RepoSettings, "cached", AsyncMock(return_value=settings))

    monkeypatch.setattr(ChangedFile, "of_pull", AsyncMock(return_value=[]))
    monkeypatch.setattr(HumanReviewRequest, "author_mention", AsyncMock(return_value="@ada"))

    _, blocks = await ReviewCard(approval).render(None)
    assert "kitchen" not in str(blocks)
    settings.review_channel = ""
    _, blocks = await ReviewCard(approval).render(None)
    assert "kitchen" in str(blocks)


def test_card_draws_only_unexcluded_hunks_and_lists_every_exclusion_by_guideline() -> None:
    generated = "\n".join(f"+GEN_{index} = {index}" for index in range(12))
    app = ChangedFile(
        filename="src/app.py",
        additions=16,
        deletions=1,
        patch=(
            "@@ -1,3 +1,3 @@\n-DEBUG = True\n+DEBUG = False\n a\n b\n"
            f"@@ -20,2 +20,14 @@\n c\n d\n{generated}\n"
            "@@ -50,2 +62,5 @@\n e\n f\n+x = 1\n+y = 2\n+z = 3"
        ),
    )
    notes = "\n".join(f"+- note {index}" for index in range(30))
    changelog = ChangedFile(
        filename="CHANGELOG.md", additions=30, patch=f"@@ -1 +1,31 @@\n # Changelog\n{notes}"
    )
    tests = ChangedFile(filename="tests/test_app.py", additions=5, patch="@@ -0,0 +1,5 @@")
    files = [app, changelog, tests]
    excluded = [
        hunk
        for exclusion in (
            Exclusion(path="src/app.py", hunks=[20], guideline="Generated constants", reason="r"),
            Exclusion(path="src/app.py", hunks=[62], guideline="Logging config", reason="r"),
            Exclusion(path="CHANGELOG.md", guideline="Release notes", reason="r"),
        )
        for hunk in exclusion.resolve(files)
    ]

    pr = PullRequest(owner="lc", repo="repo", number=7)
    approval = HumanReviewRequest(
        pull_request_id=pr.id, head_sha="abc", kind="expedited", excluded_hunks=excluded
    )
    approval.pull_request = pr
    _, blocks = open_card(approval, title="t", author="<@U1>", files=files)
    texts: list[str] = []
    for block in blocks:
        match block["type"]:
            case "section":
                texts.append(block["text"]["text"])
            case "context":
                texts.extend(element["text"] for element in block["elements"])

    drawn = [text for text in texts if text.startswith("```")]
    assert drawn == ["```\n@@ -1,3 +1,3 @@\n-DEBUG = True\n+DEBUG = False\n a\n b\n```"]
    assert "`src/app.py`  +1 −1" in texts
    assert texts[-3:-1] == [
        "*Tests (not shown)*\n`tests/test_app.py`  +5 −0",
        "*Open SWE judged these auto-approvable under `.open-swe/APPROVALS.md` (not shown)*\n"
        "_Generated constants_: `src/app.py` 1 hunk +12 −0\n"
        "_Logging config_: `src/app.py` 1 hunk +3 −0\n"
        "_Release notes_: `CHANGELOG.md` +30 −0",
    ]
