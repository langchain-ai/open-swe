import json
from unittest.mock import AsyncMock

import pytest

from openswe.expedited_review.card import _diffstat, closed_card, open_card, readiness_prompt
from openswe.expedited_review.eligibility import ChangedFile, Exclusion
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.github.repositories import Repository
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.slack import client as slack_client
from openswe.slack.blocks import MESSAGE_MAX_BLOCKS, SECTION_TEXT_MAX_CHARS, block_payload, context
from openswe.slack.cards import repost_thread_card
from openswe.users.models import User, UserIdentity


@pytest.mark.parametrize(
    ("display_name", "expected"),
    [("Grace <@U_OTHER> & Co", "Grace &lt;@U_OTHER&gt; &amp; Co"), ("", "grace")],
)
@pytest.mark.parametrize("state", ["open", "merged", "cancelled"])
@pytest.mark.parametrize("author_source", ["github", "requester", "missing"])
async def test_collapsed_card_names_author_and_approver_without_pinging_them(
    display_name: str,
    expected: str,
    state: str,
    author_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        display_name=display_name,
        identities=[
            UserIdentity(provider="github", external_id="2", login="grace"),
            UserIdentity(provider="slack", external_id="U_GRACE"),
        ],
    )
    vote = HumanReviewParticipant(user_id=user.id)
    vote.user = user
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    approval = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="expedited")
    approval.pull_request = pr
    approval.participants = [vote]

    if author_source == "requester":
        pr.author = "robin-bot"
        requester = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])
        pr.author_user_id = requester.id
        monkeypatch.setattr(User, "get", AsyncMock(return_value=requester))
    elif author_source == "missing":
        pr.author = ""
    author_login = await approval.author_login()
    approval.state = state
    if state == "open":
        text, blocks = open_card(
            approval, title="t", author="<@U_ADA>", author_login=author_login, files=[]
        )
    else:
        text, blocks = closed_card(
            approval,
            title="t",
            author="<@U_ADA>",
            author_login=author_login,
            files=[],
            outcome=f"{state}. Approved by {expected}.",
        )

    assert ("Author ada" in text) == (author_source != "missing")
    assert ("Author ada" in blocks[0]["text"]["text"]) == (author_source != "missing")
    assert "Author " not in text if author_source == "missing" else "robin-bot" not in text
    assert f"Approved by {expected}." in text
    assert f"Approved by {expected}." in blocks[0]["text"]["text"]
    assert "<@" not in text
    assert "<@" not in str(blocks)


def test_long_test_paths_stay_under_the_slack_limit_and_count_the_rest() -> None:
    tests = [
        ChangedFile(filename=f"tests/{'deep/' * 28}test_{index}.py", additions=3, patch="+x")
        for index in range(20)
    ]

    [block] = _diffstat("Tests", "test files", tests)
    text = block["text"]["text"]

    assert block["expand"] is False
    assert len(text) <= SECTION_TEXT_MAX_CHARS
    shown = text.count("test_")
    assert 0 < shown < len(tests)
    assert text.endswith(f"{len(tests) - shown} more test files on GitHub.")


@pytest.mark.parametrize("base_ref", ["main", "release"])
@pytest.mark.parametrize("surface", ["thread", "copy", "dm"])
@pytest.mark.parametrize("agent_thread", [False, True])
async def test_configured_channel_hides_send_controls(
    monkeypatch: pytest.MonkeyPatch,
    github_app: AsyncMock,
    base_ref: str,
    surface: str,
    agent_thread: bool,
) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7, base_ref=base_ref)
    approval = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        slack_channel_choices=[{"id": "C1", "name": "kitchen"}],
        thread_id="agent-thread" if agent_thread else "",
        slack_channel_id="C1",
        slack_thread_ts="1.0",
        slack_message_ts="2.0",
    )
    approval.pull_request = pr
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://robin.example")
    settings = RepoSettings(review_channel="C2")
    monkeypatch.setattr(RepoSettings, "cached", AsyncMock(return_value=settings))

    files = [
        ChangedFile(filename=f"src/app{index}.py", additions=1, patch="+x") for index in range(21)
    ]
    monkeypatch.setattr(ChangedFile, "of_pull", AsyncMock(return_value=files))
    monkeypatch.setattr(HumanReviewRequest, "author_mention", AsyncMock(return_value="@ada"))
    monkeypatch.setattr(Repository, "resolve_default_branch", AsyncMock(return_value="main"))
    monkeypatch.setattr(
        "openswe.human_review.lifecycle.origin_footer",
        AsyncMock(return_value=[context("Origin thread")]),
    )
    monkeypatch.setattr(
        "openswe.human_review.lifecycle.get_slack_permalink",
        AsyncMock(return_value="https://langchain.slack.com/archives/C1/p123456"),
    )

    post = AsyncMock(return_value="3.0")
    monkeypatch.setattr(slack_client, "_post_slack_message_with_ts", post)
    monkeypatch.setattr("openswe.slack.cards.delete_slack_message", AsyncMock(return_value=True))
    monkeypatch.setattr(ReviewCard, "_diff_image_id", AsyncMock(return_value=None))
    for configured in (True, False):
        settings.review_channel = "C2" if configured else ""
        text, blocks = await ReviewCard(approval).render(
            None, copy=surface == "copy", dm=surface == "dm"
        )
        fits = (
            base_ref == "main"
            or (surface == "thread" and (not agent_thread or configured))
            or (surface == "dm" and configured)
        )
        assert len(blocks) <= MESSAGE_MAX_BLOCKS
        assert ("kitchen" in str(blocks)) is (not configured and surface != "copy" and fits)
        assert ("Targets non-default branch" in str(blocks)) is (base_ref != "main")
        assert ("Origin thread" in str(blocks)) is (surface != "thread")
        assert ("open_swe_option_select_approve" in str(blocks)) is fits
        if surface != "thread":
            continue
        assert await repost_thread_card(
            (approval.slack_channel_id, approval.slack_thread_ts),
            approval.slack_message_ts,
            text,
            blocks,
            broadcast=True,
            agent_thread_id=approval.thread_id or None,
            adopt=AsyncMock(return_value=True),
        )
        posted = post.call_args.kwargs["blocks"]
        assert posted == block_payload(blocks)
        assert len(posted) <= MESSAGE_MAX_BLOCKS
        assert ("open_swe_option_select_approve" in str(posted)) is fits
        await ReviewCard(approval).post_expedited(title=pr.title, files=files)
        posted = post.call_args.kwargs["blocks"]
        assert len(posted) <= MESSAGE_MAX_BLOCKS
        assert ("open_swe_option_select_approve" in str(posted)) is fits
        if agent_thread:
            assert str(posted).count("https://robin.example/agents/agent-thread") == 1


@pytest.mark.parametrize("condition", [False, True])
def test_card_draws_unexcluded_hunks_and_summarizes_omissions(condition: bool) -> None:
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
    for entry in excluded:
        entry["approval_condition"] = condition
    approval = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        excluded_hunks=excluded,
        tldr="Additional PR summary",
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

    drawn = [
        block["elements"][0]["elements"][0]["text"]
        for block in blocks
        if block["type"] == "rich_text"
    ]
    assert drawn == ["@@ -1,3 +1,3 @@\n-DEBUG = True\n+DEBUG = False\n a\n b"]
    assert "`src/app.py`  +1 −1" in texts
    assert (">Additional PR summary" in texts) is not condition
    omitted = [
        block for block in blocks if block["type"] == "section" and block.get("expand") is False
    ]
    assert len(omitted) == 2
    details = "\n".join(block["text"]["text"] for block in omitted)
    assert "`tests/test_app.py`  +5 −0" in details
    assert "`src/app.py`" in details
    assert "`CHANGELOG.md`" in details
    assert ("Fully described by the change above" in details) is condition
    assert ("Generated constants" in details) is not condition
    assert texts[-1] == f"<{pr.url}/files|Review the full diff and omitted files>"


@pytest.mark.parametrize("shown_count", [17, 21])
def test_fallback_card_fits_slack_block_limit_with_every_omitted_group(shown_count: int) -> None:
    shown = [
        ChangedFile(filename=f"src/module_{index}.py", additions=1, patch="@@ -0,0 +1 @@\n+x")
        for index in range(shown_count)
    ]
    files = [
        *shown,
        ChangedFile(filename="tests/test_app.py", additions=1, patch="+x"),
        ChangedFile(filename="swagger.json", additions=1, patch="+x"),
        ChangedFile(filename="docs/a.md", additions=1, patch="@@ -0,0 +1 @@\n+a"),
        ChangedFile(filename="docs/b.md", additions=1, patch="@@ -0,0 +1 @@\n+b"),
    ]
    excluded = [
        *Exclusion(path="docs/a.md", guideline="Docs", reason="r").resolve(files),
        *Exclusion(path="docs/b.md", guideline="Docs", reason="r").resolve(files),
    ]
    excluded[0]["approval_condition"] = True
    pr = PullRequest(owner="lc", repo="repo", number=7)
    approval = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        excluded_hunks=excluded,
        slack_channel_id="C1",
    )
    approval.pull_request = pr
    choices = [{"id": "C1", "name": "kitchen"}, {"id": "C2", "name": "reviews"}]

    _, blocks = open_card(approval, title="t", author="<@U1>", files=files, choices=choices)  # type: ignore[arg-type]

    assert len(blocks) <= MESSAGE_MAX_BLOCKS
    if shown_count == 21:
        assert "Too large to approve inline" in str(blocks)
        assert "open_swe_option_select_approve" not in str(blocks)
        assert pr.url in str(blocks)
    else:
        drawn = [block for block in blocks if block["type"] == "rich_text"]
        assert len(drawn) == len(shown)
        assert "open_swe_option_select_approve" in str(blocks)
        assert f"<{pr.url}/files|Review the full diff and omitted files>" in str(blocks)
        assert blocks[-1]["block_id"] == "expedited_review_send"


@pytest.mark.parametrize("condition", [False, True])
def test_inline_approval_keeps_long_descriptions_and_every_patch_record(condition: bool) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    description = "Approve the complete change & its consequences. " * 100
    approval = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        tldr=description,
        excluded_hunks=[
            {
                "path": "src/covered.py",
                "digest": "covered",
                "header": "",
                "additions": 1,
                "deletions": 0,
                "guideline": description,
                "reason": description,
                "approval_condition": True,
            }
        ]
        if condition
        else [],
    )
    approval.pull_request = pr
    patch = "@@ -1,80 +1,81 @@\n" + " context\n" * 80 + "+" + "<value>" * 500
    _, blocks = open_card(
        approval,
        title="t",
        author="<@U1>",
        files=[ChangedFile(filename="src/app.py", additions=1, patch=patch)],
    )
    sections = [block["text"]["text"] for block in blocks if block["type"] == "section"]
    escaped = description.replace("&", "&amp;").strip()
    if condition:
        assert not any(text.startswith(">") for text in sections)
        assert escaped in "".join(text.removeprefix("*You're approving:* ") for text in sections)
        covered = [
            block["elements"][0]["text"].removeprefix("Covered by auto-approve: ")
            for block in blocks
            if block["type"] == "context"
        ]
        assert escaped in "".join(covered)
    else:
        assert escaped in "".join(text.removeprefix(">") for text in sections)
    assert all(len(text) <= SECTION_TEXT_MAX_CHARS for text in sections)
    drawn = [
        block["elements"][0]["elements"][0]["text"]
        for block in blocks
        if block["type"] == "rich_text"
    ]
    assert "\n".join(drawn) == patch

    files = [
        ChangedFile(filename=f"src/{'deep/' * 20}app{index}.py", additions=1, patch="+x")
        for index in range(25)
    ]
    _, oversized = open_card(approval, title="t", author="<@U1>", files=files)
    assert "Too large to approve inline" in str(oversized)
    assert "open_swe_option_select_approve" not in str(oversized)

    _, draft = readiness_prompt(approval, title="t", author="<@U1>", files=files)
    assert "Too large to display inline" in str(draft)
    assert "open_swe_option_select_ready" in str(draft)
    assert "open_swe_option_select_approve" not in str(draft)

    approval.tldr = ""
    approval.excluded_hunks = []
    for max_blocks in (MESSAGE_MAX_BLOCKS - 2, MESSAGE_MAX_BLOCKS):
        _, boundary = open_card(
            approval,
            title="t",
            author="<@U1>",
            files=files[:22],
            max_blocks=max_blocks,
        )
        assert len(boundary) <= max_blocks
        assert ("open_swe_option_select_approve" in str(boundary)) is (
            max_blocks == MESSAGE_MAX_BLOCKS
        )
        _, draft = readiness_prompt(
            approval, title="t", author="<@U1>", files=files[:21], max_blocks=max_blocks
        )
        assert len(draft) <= max_blocks
        assert ("Too large to display inline" in str(draft)) is (max_blocks < MESSAGE_MAX_BLOCKS)
        assert "open_swe_option_select_ready" in str(draft)
        _, closed = closed_card(
            approval,
            title="t",
            author="<@U1>",
            files=files[:22],
            outcome="Dismissed",
            max_blocks=max_blocks,
        )
        assert len(closed) <= max_blocks
        assert ("rich_text" in str(closed)) is (max_blocks == MESSAGE_MAX_BLOCKS)

    _, closed = closed_card(approval, title="t", author="<@U1>", files=files, outcome="Dismissed")
    assert len(closed) <= 50
    assert "Dismissed" in str(closed)
    assert "actions" not in str(closed)

    _, pictured = open_card(
        approval, title="t", author="<@U1>", files=files, diff_image_id="F_DIFF"
    )
    [picture] = [block for block in pictured if block["type"] == "image"]
    assert len(picture["alt_text"]) <= 2000


def test_send_controls_are_buttons_trigger_server_forwards() -> None:
    """No select: Trigger Server forwards none; "Other channel…" opens its picker itself."""
    from openswe.expedited_review.card import _send_controls  # noqa: PLC0415
    from openswe.slack.app_modals import VALUE_LIMIT  # noqa: PLC0415

    pr = PullRequest(owner="lc", repo="repo", number=7)
    approval = HumanReviewRequest(
        pull_request_id=pr.id, head_sha="abc", kind="expedited", slack_channel_id="C1"
    )
    choices = [{"id": "C1", "name": "kitchen"}, {"id": "C2", "name": "reviews"}]

    [block] = _send_controls(approval, choices)  # type: ignore[arg-type]

    broadcast, other = block["elements"]
    assert broadcast["type"] == other["type"] == "button"
    assert "url" not in broadcast and "url" not in other
    assert other["action_id"] == f"mda_modal::expedited_review_channel:{approval.id}"
    assert len(other["value"]) <= VALUE_LIMIT
    view = json.loads(other["value"])
    assert {"callback_id", "private_metadata"}.isdisjoint(view)
    [picker] = view["blocks"]
    # Required: Trigger Server answers Slack, so the backend can't send errors back.
    assert picker["element"]["type"] == "conversations_select"
    assert not picker.get("optional")
