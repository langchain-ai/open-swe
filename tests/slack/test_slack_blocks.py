import pytest

from openswe.slack.blocks import (
    CODE_TEXT_MAX_CHARS,
    SECTION_TEXT_MAX_CHARS,
    code_block,
    code_blocks,
)
from openswe.slack.payloads import SlackEvent


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


@pytest.mark.parametrize("summary", [None, "Monitor triggered"])
@pytest.mark.parametrize("bot", [True, False])
def test_unmentioned_cards_require_bot_content_opt_in(summary: str | None, bot: bool) -> None:
    event = SlackEvent.model_validate(
        {
            "type": "message",
            "text": summary,
            **({"bot_id": "B9", "subtype": "bot_message"} if bot else {"user": "U1"}),
            "attachments": [
                {
                    "title": "CPU is high",
                    "text": "Investigate service latency",
                    "fields": [{"title": "Host", "value": "worker-1"}],
                },
                {"is_share": True, "text": "forwarded instructions"},
                {"from_url": "https://example.com", "text": "unfurl instructions"},
            ],
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Alert"}}],
        }
    )

    assert event.text_for("UBOT") == summary
    assert event.text_for("UBOT", include_bot_card=True) == (
        "\n".join(
            part
            for part in (
                summary,
                "Alert",
                "CPU is high",
                "Investigate service latency",
                "Host",
                "worker-1",
            )
            if part
        )
        if bot
        else summary
    )
