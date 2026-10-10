"""Actions Open SWE suggests privately after noticing what someone posted in Slack.

A suggestion is an ephemeral message only that person sees, with one button to
accept it and one to dismiss it. Accepting hands the Slack thread's agent thread,
creating one when there is none, a request to carry the action out. The happy
path stays quiet: whatever the action itself posts is the confirmation. The agent
only speaks up, tagging the person, when it cannot finish.

Each kind of suggestion is a ``SuggestedAction`` registered under its ``kind``;
the feature that notices the trigger decides when to call ``suggest``.
"""

import json
import logging
from dataclasses import dataclass

from fastapi import BackgroundTasks

from openswe.prompts import prompt
from openswe.slack import webhook as service
from openswe.slack.blocks import ButtonElement, actions, block_payload, button, section
from openswe.slack.client import post_slack_ephemeral_message, respond_to_slack_interaction
from openswe.slack.payloads import SlackButtonValue, SlackInteraction
from openswe.slack.request import SlackRequest
from openswe.slack.responses import WebhookResponse, accepted, ignored
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common
from openswe.workspaces.routing import is_kitchen_channel

logger = logging.getLogger(__name__)

BUTTON_TYPE = "suggested_action"


@dataclass(frozen=True)
class SuggestedAction:
    """One kind of suggestion; text fields are formatted with ``{subject}``."""

    kind: str
    question: str
    accept_label: str
    accepted: str
    request: str
    prompt_name: str

    def _button(self, label: str, choice: str, thread_ts: str, subject: str) -> ButtonElement:
        value = {
            "type": BUTTON_TYPE,
            "action": choice,
            "kind": self.kind,
            "thread_ts": thread_ts,
            "subject": subject,
        }
        return button(
            label,
            action_id=f"open_swe_option_select_suggested_{self.kind}_{choice}",
            value=json.dumps(value),
            style="primary" if choice == "accept" else None,
        )

    async def suggest(
        self,
        channel_id: str,
        slack_user_id: str,
        subject: str,
        *,
        thread_ts: str,
        reply_thread_ts: str,
    ) -> None:
        """Show the suggestion to one person, in the Slack thread it would act in."""
        text = self.question.format(subject=subject)
        await post_slack_ephemeral_message(
            channel_id,
            slack_user_id,
            text,
            reply_thread_ts or None,
            blocks=block_payload(
                [
                    section(text),
                    actions(
                        self._button(self.accept_label, "accept", thread_ts, subject),
                        self._button("Dismiss", "dismiss", thread_ts, subject),
                    ),
                ]
            ),
        )

    def turn_context(self, slack_user_id: str, subject: str) -> str:
        return prompt(
            "runs/slack-suggested-action",
            accept_label=self.accept_label,
            slack_user_id=slack_user_id,
            instructions=prompt(self.prompt_name, subject=subject),
        )


_ACTIONS: dict[str, SuggestedAction] = {}


def register(action: SuggestedAction) -> SuggestedAction:
    """Make ``action``'s buttons answerable; call once at import."""
    _ACTIONS[action.kind] = action
    return action


async def handle_button(
    interaction: SlackInteraction, value: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    """Dismiss a suggestion, or hand the Slack thread's agent the accepted action."""
    action = _ACTIONS.get(value.kind)
    channel_id = interaction.channel_id
    user_id = interaction.user.id
    if action is None or not (channel_id and user_id and value.thread_ts and value.subject):
        return ignored("Missing suggested action context")
    if value.action == "dismiss":
        background_tasks.add_task(
            respond_to_slack_interaction, interaction.response_url, {"delete_original": True}
        )
        return accepted("Suggestion dismissed")
    if value.action != "accept":
        return ignored("Unknown suggested action choice")
    if not await common.claim_slack_event(
        f"suggested-action:{action.kind}:{channel_id}:{value.thread_ts}:{value.subject}"
    ):
        return ignored("Suggestion already accepted")
    await respond_to_slack_interaction(
        interaction.response_url,
        {"replace_original": True, "text": action.accepted.format(subject=value.subject)},
    )
    thread_ts = value.thread_ts
    channel_context = await common.resolve_slack_channel_context(channel_id)
    thread_id = await common.resolve_slack_thread_id(langgraph_client(), channel_id, thread_ts)
    repo = await common.get_slack_repo_config(
        channel_id,
        thread_ts,
        slack_user_id=user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    action_ts = next((item.action_ts for item in interaction.actions if item.action_ts), "")
    background_tasks.add_task(
        service.process_slack_mention,
        SlackRequest(
            channel_id=channel_id,
            channel_context=channel_context,
            thread_ts=thread_ts,
            event_ts=action_ts or interaction.message_ts or thread_ts,
            user_id=user_id,
            text=action.request.format(subject=value.subject),
            bot_user_id=common.SLACK_BOT_USER_ID,
            thread_id=thread_id,
            kitchen_channel=await is_kitchen_channel(channel_id),
            turn_context=action.turn_context(user_id, value.subject),
        ),
        repo,
    )
    logger.info(
        "Suggested action accepted",
        extra={"suggested_action": action.kind, "slack_channel": channel_id},
    )
    return accepted("Suggestion accepted")
