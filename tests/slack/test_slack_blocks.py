from agent.slack.blocks import (
    SECTION_TEXT_MAX_CHARS,
    block_payload,
    code_block,
    escape,
    modal,
    section,
    text_input,
)
from agent.slack.payloads import SlackViewSubmission


def test_section_text_is_truncated_to_slacks_limit() -> None:
    block = section("x" * (SECTION_TEXT_MAX_CHARS + 500))

    assert len(block["text"]["text"]) == SECTION_TEXT_MAX_CHARS


def test_escaping_covers_slacks_markup_characters() -> None:
    assert escape('a & b < c > d "e"') == 'a &amp; b &lt; c &gt; d "e"'


def test_code_block_escapes_fences_and_fits_the_limit() -> None:
    fenced = code_block("```rm -rf```\n<script>", limit=200)
    oversized = code_block("y" * 5000)

    assert "```rm -rf```" not in fenced
    assert "&lt;script&gt;" in fenced
    assert len(oversized) <= SECTION_TEXT_MAX_CHARS
    assert oversized.endswith("…\n```")


def test_optional_modal_fields_are_omitted_when_not_given() -> None:
    bare = modal(callback_id="c", title="t", blocks=[section("body")])
    full = modal(
        callback_id="c", title="t", blocks=[section("body")], submit="Go", private_metadata="{}"
    )

    assert "submit" not in bare and "private_metadata" not in bare
    assert full["submit"] == {"type": "plain_text", "text": "Go", "emoji": True}
    assert block_payload(full["blocks"]) == [
        {"type": "section", "text": {"type": "mrkdwn", "text": "body"}}
    ]


def test_input_block_carries_only_the_options_it_was_given() -> None:
    minimal = text_input(block_id="b", label="L", action_id="a")
    detailed = text_input(
        block_id="b", label="L", action_id="a", multiline=True, max_length=10, optional=True
    )

    assert "optional" not in minimal and "multiline" not in minimal["element"]
    assert detailed["optional"] is True
    assert detailed["element"]["multiline"] is True and detailed["element"]["max_length"] == 10


def test_view_submission_reads_metadata_and_typed_values() -> None:
    submission = SlackViewSubmission.parse(
        {
            "type": "view_submission",
            "user": {"id": "U1", "username": "ada"},
            "view": {
                "callback_id": "expedited_review_reject",
                "private_metadata": '{"approval_id": "abc"}',
                "state": {"values": {"feedback": {"comment": {"value": "needs a test"}}}},
            },
        }
    )

    assert submission is not None
    assert submission.callback_id == "expedited_review_reject"
    assert submission.metadata == {"approval_id": "abc"}
    assert submission.submitted("feedback", "comment") == "needs a test"


def test_missing_or_empty_submission_fields_read_as_empty() -> None:
    submission = SlackViewSubmission.parse(
        {
            "type": "view_submission",
            "view": {"private_metadata": "not json", "state": {"values": {"b": {"a": {}}}}},
        }
    )

    assert submission is not None
    assert submission.metadata == {}
    assert submission.submitted("b", "a") == ""
    assert submission.submitted("nope", "a") == ""
