"""Where a message in a run's conversation came from, recorded on it by whoever built it.

The backend builds every message a person or system sends a run, and every introduction
and piece of run guidance it adds (``openswe.input_messages``); MDA's loop builds the
guidance it adds before a model call. Each records its provenance under
``PROVENANCE_KEY`` in the message's ``additional_kwargs``, and code that asks what a
message is, above all whether a person wrote it, reads it from there.

The ``<input-message>`` and ``<dynamic-context>`` text the model reads renders the same
facts for the model. It is read back only for messages stored before they carried
provenance (``Provenance.of``).

Clients never set it: the backend rebuilds every message a client sends before a run
receives it, so a human message's provenance is the backend's word that a person wrote it.
"""

from collections.abc import Mapping
from typing import Final, Literal, Protocol, Self
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict

PROVENANCE_KEY: Final = "open_swe_provenance"

type ProvenanceKind = Literal["human", "system", "context"]


class _Message(Protocol):
    """A chat message, as LangChain's message classes carry one."""

    @property
    def type(self) -> str: ...

    @property
    def content(self) -> object: ...

    @property
    def additional_kwargs(self) -> Mapping[str, object]: ...


class Provenance(BaseModel):
    """What a message in a run's conversation is, and whom it is from."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    # ``human``: what a person wrote. ``system``: an event sent to the agent, such as a
    # bot's alert or a background task's report. ``context``: what the harness tells the
    # model about the conversation or the run: an introduction, run guidance, a nudge's
    # sender, the task a thread belongs to.
    kind: ProvenanceKind
    # The entity the message is from, such as ``github:alice`` or ``system:reminder``; for
    # an introduction, the entity it introduces.
    sender: str
    # Where a human or system message arrived: ``slack``, ``web``, ``automation``...
    surface: str | None = None
    # The conversation it arrived in, by its entity id.
    channel: str | None = None

    @classmethod
    def harness(cls, source: str) -> Self:
        """Context the harness adds for ``source``, such as ``run_guidance``."""
        return cls(kind="context", sender=f"open-swe:{source}")

    @property
    def human(self) -> bool:
        return self.kind == "human"

    def stamp(self, additional_kwargs: Mapping[str, object] | None = None) -> dict[str, object]:
        """``additional_kwargs`` recording this provenance."""
        return {**(additional_kwargs or {}), PROVENANCE_KEY: self.model_dump(exclude_none=True)}

    @classmethod
    def of(cls, message: _Message | Mapping[str, object]) -> Self | None:
        """What ``message`` is; None for a message no input builder made, such as a reply.

        Takes a message or its JSON form: a run input's ``{"role", "content"}`` or the
        ``{"type", "content"}`` of saved state.
        """
        if isinstance(message, Mapping):
            role = message.get("type", message.get("role"))
            content = message.get("content")
            additional_kwargs = message.get("additional_kwargs")
        else:
            role, content, additional_kwargs = (
                message.type,
                message.content,
                message.additional_kwargs,
            )
        if role not in {"human", "user"}:
            return None
        stamped = (
            additional_kwargs.get(PROVENANCE_KEY)
            if isinstance(additional_kwargs, Mapping)
            else None
        )
        if stamped is not None:
            return cls.model_validate(stamped)
        return cls._rendered(content)

    @classmethod
    def _rendered(cls, content: object) -> Self | None:
        """The provenance a message stored before messages carried one renders in its text."""
        for text in cls._texts(content):
            if "<input-message" not in text and "<dynamic-context" not in text:
                continue
            try:
                root = ElementTree.fromstring(text)
            except ElementTree.ParseError:
                continue
            if root.tag == "dynamic-context" and (entity := root.get("id")):
                run = root.get("kind") == "run"
                return cls(kind="context", sender=f"run:{entity}" if run else entity)
            envelope = root if root.tag == "input-message" else root.find(".//input-message")
            if envelope is None:
                continue
            kind, sender = envelope.get("kind"), envelope.get("sender")
            if kind in {"human", "system"} and sender:
                return cls(
                    kind="human" if kind == "human" else "system",
                    sender=sender,
                    surface=envelope.get("surface"),
                    channel=envelope.get("channel"),
                )
        return None

    @staticmethod
    def _texts(content: object) -> list[str]:
        values = content if isinstance(content, list) else [content]
        texts = (value.get("text") if isinstance(value, Mapping) else value for value in values)
        return [text for text in texts if isinstance(text, str)]
