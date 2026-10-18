"""What every kind of slacklet is made of: a state and the typed commands that change it.

A kind's state renders the slacklet message; its commands are the only way the
state changes. Applying a slacklet's commands in order from the first rebuilds
its state exactly, so the command log, not the state, is what is permanent.
"""

from abc import abstractmethod
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from openswe.slack.blocks import Block


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SlackletState(Frozen):
    """A slacklet's state, which its message shows."""

    @abstractmethod
    def describe(self) -> str:
        """Which slacklet this is, for the agent, e.g. "the pull request slacklet for <url>"."""

    @abstractmethod
    def blocks(self, *, seq: int, op: str) -> list[Block]:
        """The slacklet message for this state, reached by command ``seq``, an ``op``."""

    @abstractmethod
    def fallback_text(self) -> str:
        """The message's notification text."""


class SlackletCommand(Frozen):
    """One typed change to a slacklet's state; subclasses fix ``op`` to a literal."""

    op: str

    @abstractmethod
    def summary(self) -> str:
        """The command for people, as its thread reply reads."""


class SlackletRefused(ValueError):
    """A kind cannot start a slacklet on this subject; the message says why."""


class Entry[S: SlackletState, C: SlackletCommand](NamedTuple):
    """One applied command and the state it left."""

    seq: int
    command: C
    state: S

    def log_text(self) -> str:
        return f"`#{self.seq} {self.command.op}` {self.command.summary()}"
