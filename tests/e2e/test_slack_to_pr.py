"""End-to-end: Slack mention → agent → scripted LLM response → bot reply.

``test_webhook_gate`` — UI-only. Verifies the implicit-mention gate:
    1. ``<@openswe-bot>`` mention → webhook accepts
    2. thread reply with no mention → still accepts (bot-participant cache)
    3. bare channel chatter → rejected

``test_slack_mention_round_trip`` — full E2E through the scripted LLM proxy:
    POST a canned assistant reply to ``/anthropic/ui/script`` with a test_id,
    send a Slack mention containing that test_id, wait for the bot to post
    the scripted reply into the Slack thread.

There is no recording or replay-by-hash — every LLM call must be covered
by a script entry or the proxy returns ``502 scripted_response_missing``.
"""

from __future__ import annotations

import os
import re
import uuid

import httpx
import pytest
from playwright.sync_api import Page, expect

FAKE_DEPS_URL = os.environ.get("FAKE_DEPS_URL", "http://localhost:13765")
BOT_USER_ID = os.environ.get("SLACK_BOT_USER_ID", "BOPENSWE01")


def _pick_bot_mention(page: Page, composer_sel: str) -> None:
    composer = page.locator(composer_sel)
    composer.click()
    composer.fill("")
    composer.type("@open")
    page.locator(".mention-item", has_text="openswe-bot").first.click()


def _send_slack(page: Page, composer_sel: str, text: str) -> None:
    composer = page.locator(composer_sel)
    composer.type(text)
    composer.press("Enter")


def _assistant_slack_reply(text: str, *, end_turn: bool = True) -> dict:
    """Build a canned Anthropic message that calls slack_thread_reply."""
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-6",
        "content": [
            {
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": "slack_thread_reply",
                "input": {"message": text},
            }
        ],
        "stop_reason": "end_turn" if end_turn else "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def test_webhook_gate(page: Page, fake_deps_url: str, reset_fake_state: None) -> None:
    """Slack UI → agent webhook gate behaves correctly across three paths."""
    page.goto(fake_deps_url)
    page.wait_for_selector("#slack-composer")

    _pick_bot_mention(page, "#slack-composer")
    _send_slack(page, "#slack-composer", "list files in /tmp")
    expect(page.locator("#status")).to_contain_text("accepted")

    page.locator(".msg").last.hover()
    page.locator(".msg").last.locator("button", has_text="Reply in thread").click()
    _send_slack(page, "#slack-thread-composer", "and also show disk usage")
    expect(page.locator("#status")).to_contain_text("accepted")

    page.locator(".close", has_text="×").click()
    _send_slack(page, "#slack-composer", "random chatter with no mention")
    expect(page.locator("#status")).to_contain_text("ignored")


def test_slack_mention_round_trip(
    page: Page, fake_deps_url: str, reset_fake_state: None
) -> None:
    """Scripted reply lands in the Slack thread after a mention."""
    test_id = f"tid-{uuid.uuid4().hex[:10]}"
    scripted_reply = (
        f"Got it (test_id={test_id}). Here is your scripted reply from the "
        "fake LLM."
    )
    httpx.post(
        f"{FAKE_DEPS_URL}/anthropic/ui/script",
        json={
            "test_id": test_id,
            "responses": [_assistant_slack_reply(scripted_reply)],
        },
        timeout=2.0,
    ).raise_for_status()

    page.goto(fake_deps_url)
    page.wait_for_selector("#slack-composer")
    _pick_bot_mention(page, "#slack-composer")
    _send_slack(page, "#slack-composer", f"[{test_id}] hello bot")

    expect(page.locator("#status")).to_contain_text("accepted")

    # Open the thread for the user's top-level mention — bot replies land
    # in the thread pane, not the channel feed.
    page.locator(".msg").last.hover()
    page.locator(".msg").last.locator("button", has_text="Reply in thread").click()

    bot_reply = page.locator("#slack-thread-body .msg.bot .body")
    expect(bot_reply.first).to_contain_text(test_id)


def _slackv2_enabled() -> bool:
    raw = os.environ.get("OPENSWE_OPTIONS", "")
    tokens = {t for t in raw.replace(",", " ").split() if t}
    return "slackv2" in tokens


@pytest.mark.skipif(
    not _slackv2_enabled(),
    reason="OPENSWE_OPTIONS does not include 'slackv2'",
)
def test_slack_assistant_thread_status(
    page: Page, fake_deps_url: str, reset_fake_state: None
) -> None:
    """When the option is on, a mention sets the Assistant thread status
    ("openswe-bot is thinking...") and the status clears after the bot replies."""
    test_id = f"tid-{uuid.uuid4().hex[:10]}"
    httpx.post(
        f"{FAKE_DEPS_URL}/anthropic/ui/script",
        json={
            "test_id": test_id,
            "responses": [_assistant_slack_reply(f"done (test_id={test_id})")],
        },
        timeout=2.0,
    ).raise_for_status()

    page.goto(fake_deps_url)
    page.wait_for_selector("#slack-composer")
    _pick_bot_mention(page, "#slack-composer")
    _send_slack(page, "#slack-composer", f"[{test_id}] ping")
    expect(page.locator("#status")).to_contain_text("accepted")

    page.locator(".msg").last.hover()
    page.locator(".msg").last.locator("button", has_text="Reply in thread").click()

    status_bar = page.locator("#slack-thread-status")
    expect(status_bar).to_have_class(re.compile(r"\bon\b"))
    expect(status_bar).to_contain_text("is thinking")
    expect(status_bar).to_contain_text("openswe-bot")

    expect(page.locator("#slack-thread-body .msg.bot .body").first).to_contain_text(test_id)
    expect(status_bar).not_to_have_class(re.compile(r"\bon\b"))


@pytest.mark.skipif(
    not _slackv2_enabled(),
    reason="DM top-level reply behavior requires 'slackv2'",
)
def test_slack_dm_no_mention_required(
    page: Page, fake_deps_url: str, reset_fake_state: None
) -> None:
    """In a DM channel, the bot replies without needing to be @mentioned,
    and (with slackv2) posts the reply as a top-level message, not a thread reply."""
    test_id = f"tid-{uuid.uuid4().hex[:10]}"
    scripted_reply = f"DM ack (test_id={test_id})"
    httpx.post(
        f"{FAKE_DEPS_URL}/anthropic/ui/script",
        json={
            "test_id": test_id,
            "responses": [_assistant_slack_reply(scripted_reply)],
        },
        timeout=2.0,
    ).raise_for_status()

    page.goto(fake_deps_url)
    page.wait_for_selector("#slack-composer")

    # Click the DM with the bot in the sidebar.
    page.locator("#slack-dm-list .item", has_text="openswe-bot").click()
    expect(page.locator("#slack-channel-title")).to_contain_text("openswe-bot")

    # Plain message — no @mention anywhere.
    _send_slack(page, "#slack-composer", f"[{test_id}] plain DM, no mention")
    expect(page.locator("#status")).to_contain_text("accepted")

    expect(
        page.locator("#slack-messages .msg.bot .body").last
    ).to_contain_text(test_id)
