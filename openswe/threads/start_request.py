"""One run asked of a thread, however it was asked for: by ``/commands``, by the runs
routes ``@langchain/react`` calls, or by an answer to the thread's interrupt."""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal, get_args

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue

from openswe.dispatch import SERVER_STREAM, RunFormat
from openswe.utils.dashboard_handoff import RELAYED_FROM_KEY

MultitaskStrategy = Literal["reject", "interrupt", "rollback", "enqueue"]


class RunRefused(HTTPException):
    """A run its thread, or the thread's runtime, would not start.

    Its ``status_code`` is the HTTP status a runs API client gets, the runtime's own when
    it refused. ``code`` is the error ``/commands`` answers with in its
    ``{"type": "error"}`` reply; a refusal without one it answers with the status, as every
    other route does.
    """

    def __init__(self, status: int, detail: str, *, code: str | None = None) -> None:
        super().__init__(status, detail)
        self.detail = detail
        self.code = code


class SentRun(BaseModel):
    """The run a message ended up in: its own, the live run it steered into, or one queued
    behind that.

    Only the fields that say which are set; :meth:`reply` is what ``/commands`` answers
    a ``run.start`` with.
    """

    thread_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    steered: bool = False
    queued: bool = False

    def reply(self) -> dict[str, JsonValue]:
        return self.model_dump(exclude_unset=True)


@dataclass
class StartRequest:
    """One run asked of a thread, before the backend builds it for whoever asked.

    ``configurable`` holds the keys the client chose, which the backend reads only some
    of. ``command`` answers the thread's interrupt instead of sending ``input``. A run
    streams in ``run_format``: the server's unless the client chose its own.
    """

    input: JsonValue = None
    configurable: dict[str, JsonValue] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    multitask_strategy: MultitaskStrategy | None = None
    command: dict[str, JsonValue] | None = None
    run_format: RunFormat = SERVER_STREAM

    @classmethod
    def of_command(cls, params: Mapping[str, JsonValue]) -> StartRequest:
        """The run a ``/commands`` ``run.start`` asks for. Its stream fields are the
        server's, whatever the client sent."""
        config = params.get("config")
        configurable = config.get("configurable") if isinstance(config, dict) else None
        metadata = params.get("metadata")
        strategy = next(
            (
                known
                for known in get_args(MultitaskStrategy)
                if known == params.get("multitask_strategy")
            ),
            None,
        )
        return cls(
            input=params.get("input"),
            configurable=dict(configurable) if isinstance(configurable, dict) else {},
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            multitask_strategy=strategy,
        )

    @classmethod
    def answering(cls, params: Mapping[str, JsonValue]) -> StartRequest:
        """The run a ``/commands`` ``input.respond`` asks for: one that answers each
        interrupt it names, with the state update and jump that come with them."""
        responses = params.get("responses")
        entries = responses if isinstance(responses, list) else [params]
        resume: dict[str, JsonValue] = {}
        for entry in entries:
            interrupt_id = entry.get("interrupt_id") if isinstance(entry, dict) else None
            if not isinstance(interrupt_id, str):
                raise RunRefused(
                    422, "input.respond requires an interrupt_id.", code="invalid_argument"
                )
            resume[interrupt_id] = entry.get("response") if isinstance(entry, dict) else None
        if not resume:
            raise RunRefused(
                422, "input.respond requires at least one response.", code="invalid_argument"
            )
        command: dict[str, JsonValue] = {"resume": resume}
        for key in ("update", "goto"):
            if (value := params.get(key)) is not None:
                command[key] = value
        return cls(command=command)

    def as_command(self, *, relayed_from: str | None = None) -> dict[str, JsonValue]:
        """The run as the steps that build it read it: a ``run.start`` command, naming the
        agent thread that relayed it, if one did."""
        params: dict[str, JsonValue] = {
            # Building the run rewrites its input in place, and a start that loses its
            # thread to another run is queued from this request again.
            "input": copy.deepcopy(self.input),
            "config": {"configurable": dict(self.configurable)},
            "metadata": dict(self.metadata),
        }
        if relayed_from:
            params[RELAYED_FROM_KEY] = relayed_from
        return {"method": "run.start", "params": params}

    def built_from(self, command: Mapping[str, JsonValue]) -> StartRequest:
        """This run, as built from ``command`` (:meth:`as_command`) for whoever sent it."""
        params = command.get("params")
        built = StartRequest.of_command(params if isinstance(params, dict) else {})
        return replace(
            built,
            multitask_strategy=self.multitask_strategy,
            command=self.command,
            run_format=self.run_format,
        )
