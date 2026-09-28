from agent.slack.blocks import CODE_TEXT_MAX_CHARS, code
from agent.slack.payloads import SlackViewSubmission


def test_code_keeps_markup_literal_and_fits_the_limit() -> None:
    [literal] = code("```rm -rf```\n<script>", language="diff")["elements"]
    [oversized] = code("y" * 5000)["elements"]

    assert literal["elements"][0]["text"] == "```rm -rf```\n<script>"
    assert literal["language"] == "diff"
    assert len(oversized["elements"][0]["text"]) <= CODE_TEXT_MAX_CHARS
    assert oversized["elements"][0]["text"].endswith("\n…")


def test_view_submission_reads_metadata_and_typed_values() -> None:
    submission = SlackViewSubmission.parse(
        {
            "type": "view_submission",
            "user": {"id": "U1", "username": "ada"},
            "view": {
                "callback_id": "example_modal",
                "private_metadata": '{"approval_id": "abc"}',
                "state": {"values": {"feedback": {"comment": {"value": "needs a test"}}}},
            },
        }
    )

    assert submission is not None
    assert submission.callback_id == "example_modal"
    assert submission.metadata == {"approval_id": "abc"}
    assert submission.submitted("feedback", "comment") == "needs a test"
