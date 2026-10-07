from openswe.slack.blocks import (
    CODE_TEXT_MAX_CHARS,
    SECTION_TEXT_MAX_CHARS,
    code_block,
    code_blocks,
)
from openswe.slack.payloads import SlackViewSubmission


def test_code_block_escapes_fences_and_fits_the_limit() -> None:
    fenced = code_block("```rm -rf```\n<script>", limit=200)
    oversized = code_block("y" * 5000)

    assert "```rm -rf```" not in fenced
    assert "&lt;script&gt;" in fenced
    assert len(oversized) <= SECTION_TEXT_MAX_CHARS
    assert oversized.endswith("…\n```")


def test_code_blocks_keep_markup_literal_and_split_without_losing_text() -> None:
    [literal] = code_blocks("```rm -rf```\n<script>", language="diff")
    long_body = "\n".join(["y" * 3000] * 3)
    texts = [block["elements"][0]["elements"][0]["text"] for block in code_blocks(long_body)]

    assert literal["elements"][0]["elements"][0]["text"] == "```rm -rf```\n<script>"
    assert literal["elements"][0]["language"] == "diff"
    assert all(len(text) <= CODE_TEXT_MAX_CHARS for text in texts)
    assert "\n".join(texts) == long_body


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
