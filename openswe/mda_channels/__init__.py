"""What every MDA channel's backend side shares, whatever the provider.

An MDA channel (Linear's, GitHub's, ...) receives its provider's events in the MDA
deployment, asks the backend's endpoint for that channel what each one starts, and
starts that run on a thread it owns, with the backend's run-complete webhook. Only
the deployment, holding Open SWE's shared secret, may call a channel endpoint
(:mod:`.auth`).

A thread or run belongs to a channel when its metadata names one
(``openswe.dispatch.channel_of``). The backend reads those threads as LangSmith
Studio does and never writes to them (``openswe.threads.runtimes``,
``openswe.threads.router``).

Each environment turns its channels on one at a time (``OPEN_SWE_MDA_CHANNELS``,
:func:`takes_intake`), and its MDA deployment ships only those. Where GitHub's is
off, the backend takes issue mentions itself, as it did before the channel, so
turning it off again is a configuration change, not a revert.
"""

from typing import Literal

from openswe.config import ENV

Channel = Literal["github", "linear", "slack"]


def takes_intake(channel: Channel) -> bool:
    """Whether ``channel``'s MDA channel, not the backend, takes its provider's events here."""
    return channel in {name.strip().lower() for name in ENV.OPEN_SWE_MDA_CHANNELS.get().split(",")}
