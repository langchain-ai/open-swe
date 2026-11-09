"""Reminders: notes the model reads beside a person's message when the message calls for one.

As with Claude Code's ``<system-reminder>``, a reminder is model-only context attached to
the turn it is about. Jev reads each message a person sends once, with every reminder's
question in one call, and each reminder whose signal it finds adds its note right after
that message, from a silent sender the dashboard never shows.
"""

from dataclasses import dataclass
from typing import Final

from langchain_core.messages import BaseMessage
from langchain_typesafe import Choice
from runtime_api.provenance import Provenance

from openswe.input_messages import RunMessage, input_message_text, system_input
from openswe.prompts import prompt
from openswe.utils.jev import select_jev_choices

REMINDER_SENDER: Final = "system:reminder"
MAX_REMINDER_TEXT_CHARS: Final = 8_000


@dataclass(frozen=True)
class Reminder:
    """A signal Jev looks for in what a person wrote, and the note the model reads when found."""

    name: str
    signal: str
    absent: str

    @property
    def question(self) -> Choice:
        return Choice(
            instructions=prompt(f"reminders/{self.name}/instructions"),
            criteria={
                choice: prompt(f"reminders/{self.name}/{choice}")
                for choice in (self.signal, self.absent)
            },
        )

    def message(self) -> RunMessage:
        return system_input(
            prompt(f"reminders/{self.name}/note"),
            {"sender_id": REMINDER_SENDER, "surface": "automation", "kind": "system"},
        )

    @classmethod
    async def called_for(cls, message: BaseMessage) -> list[RunMessage]:
        """The notes a person's ``message`` calls for."""
        provenance = Provenance.of(message)
        if provenance is None or not provenance.human:
            return []
        text = (input_message_text(message.content) or "")[-MAX_REMINDER_TEXT_CHARS:]
        if not text.strip():
            return []
        choices = await select_jev_choices(
            text, questions={reminder.name: reminder.question for reminder in REMINDERS}
        )
        return [
            reminder.message()
            for reminder in REMINDERS
            if choices[reminder.name] == reminder.signal
        ]


REMINDERS: Final = (Reminder("frustration", signal="frustrated", absent="not_frustrated"),)
