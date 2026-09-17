from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent.slack import interactive
from agent.slack.continuations import SlackContinuation, action_id_for, claim, save, token_in
from agent.slack.payloads import SlackBlockAction
from agent.slack.resume import describe_action


def _prepare(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Any]]:
    return interactive.prepare(
        blocks,
        thread_id="thread-1",
        channel_id="C1",
        thread_ts="1700000000.000100",
        run_config={"source": "slack"},
    )


def test_token_survives_the_action_id_round_trip() -> None:
    row = SlackContinuation(
        thread_id="t", action_id="rerun_tests", element_type="button", channel_id="C1"
    )

    assert token_in(action_id_for(row.id)) == row.id
    assert token_in("rerun_tests") is None
    assert token_in(f"{action_id_for(row.id)}-extra") is None
    assert token_in(None) is None


def test_interactive_elements_are_rewritten_and_recorded() -> None:
    prepared, rows = _prepare(
        [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Tests failed."}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Rerun tests"},
                        "action_id": "rerun_tests",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open PR"},
                        "url": "https://github.test/pr/1",
                        "action_id": "open_pr",
                    },
                    {
                        "type": "static_select",
                        "action_id": "pick_base",
                        "placeholder": {"type": "plain_text", "text": "Base branch"},
                        "options": [],
                    },
                ],
            },
        ]
    )

    elements = prepared[1]["elements"]
    assert token_in(elements[0]["action_id"]) is not None
    # A link button is Slack's own to handle, so it keeps the id the agent gave it.
    assert elements[1]["action_id"] == "open_pr"
    assert token_in(elements[2]["action_id"]) is not None
    assert prepared[0] == {"type": "section", "text": {"type": "mrkdwn", "text": "Tests failed."}}

    assert [(row.action_id, row.label, row.single_use) for row in rows] == [
        ("rerun_tests", "Rerun tests", True),
        ("pick_base", "Base branch", False),
    ]
    assert {row.thread_id for row in rows} == {"thread-1"}
    assert {row.run_config["source"] for row in rows} == {"slack"}


def test_section_image_accessory_is_left_alone() -> None:
    prepared, rows = _prepare(
        [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "coverage"},
                "accessory": {
                    "type": "image",
                    "image_url": "https://x.test/a.png",
                    "alt_text": "a",
                },
            }
        ]
    )

    assert rows == []
    assert prepared[0]["accessory"]["type"] == "image"


def test_section_button_accessory_is_registered() -> None:
    prepared, rows = _prepare(
        [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "coverage"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Explain"},
                    "action_id": "explain",
                },
            }
        ]
    )

    assert [row.action_id for row in rows] == ["explain"]
    assert token_in(prepared[0]["accessory"]["action_id"]) is not None


def test_input_blocks_are_refused() -> None:
    with pytest.raises(interactive.UnsupportedBlocks, match="modal"):
        _prepare(
            [
                {
                    "type": "input",
                    "block_id": "why",
                    "label": {"type": "plain_text", "text": "Why"},
                    "element": {"type": "plain_text_input", "action_id": "why"},
                }
            ]
        )


def test_layout_only_blocks_need_no_database() -> None:
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "done"}},
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open PR"},
                    "url": "https://github.test/pr/1",
                    "action_id": "open_pr",
                }
            ],
        },
    ]

    assert interactive.has_interactive_element(blocks) is False
    assert (
        interactive.has_interactive_element(
            [{"type": "actions", "elements": [{"type": "button", "action_id": "go"}]}]
        )
        is True
    )


@pytest.mark.parametrize(
    "action,expected",
    [
        (
            SlackBlockAction(
                action_id="a", type="button", text={"type": "plain_text", "text": "Rerun tests"}
            ),
            "clicked *Rerun tests*",
        ),
        (
            SlackBlockAction(
                action_id="a",
                type="static_select",
                selected_option={"text": {"type": "plain_text", "text": "main"}, "value": "main"},
            ),
            "chose `main`",
        ),
        (
            SlackBlockAction(action_id="a", type="datepicker", selected_date="2026-09-17"),
            "chose `2026-09-17`",
        ),
        (SlackBlockAction(action_id="a", type="button"), "interacted with it"),
    ],
)
def test_the_agent_is_told_what_the_person_did(action: SlackBlockAction, expected: str) -> None:
    assert describe_action(action) == expected


def _row(**overrides: Any) -> SlackContinuation:
    values: dict[str, Any] = {
        "thread_id": "thread-1",
        "action_id": "rerun_tests",
        "element_type": "button",
        "channel_id": "C1",
        "message_ts": "1700000000.000200",
    }
    return SlackContinuation(**{**values, **overrides})


@pytest.mark.usefixtures("registry_db")
async def test_only_the_first_click_claims_a_single_use_element() -> None:
    row = _row()
    await save([row])

    assert await claim(row.id, slack_user_id="U1") is not None
    assert await claim(row.id, slack_user_id="U2") is None


@pytest.mark.usefixtures("registry_db")
async def test_claiming_one_answer_revokes_the_alternatives() -> None:
    chosen, sibling, elsewhere = _row(), _row(action_id="skip"), _row(message_ts="1700.000300")
    await save([chosen, sibling, elsewhere])

    assert await claim(chosen.id, slack_user_id="U1") is not None
    assert await claim(sibling.id, slack_user_id="U1") is None
    # A different message keeps its own elements.
    assert await claim(elsewhere.id, slack_user_id="U1") is not None


@pytest.mark.usefixtures("registry_db")
async def test_a_select_can_be_changed_again() -> None:
    row = _row(element_type="static_select", single_use=False)
    await save([row])

    assert await claim(row.id, slack_user_id="U1") is not None
    assert await claim(row.id, slack_user_id="U1") is not None


@pytest.mark.usefixtures("registry_db")
async def test_an_expired_element_is_not_claimable() -> None:
    row = _row(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    await save([row])

    assert await claim(row.id, slack_user_id="U1") is None
