"""How a revision of MDA names its calls on the agent API and reads their answers.

A run keeps calling the backend as the MDA revision it started on did, for as long
as it runs, while the backend rolls out underneath it. ``CURRENT`` is the agent API
as ``runtime_api`` defines it. Revisions from before it called the loop's
calls at ``REMOTE_RUNTIME_HOOKS_PATH``, as ``REMOTE_RUNTIME_HOOKS`` translates them,
and the tools' calls, unchanged, at ``REMOTE_RUNTIME_API_PATH``. Both are served by
the same endpoints behind the same run token until no run of those revisions is left.
"""

from typing import Final

from pydantic import JsonValue
from runtime_api.loop import BEFORE_MODEL, SANDBOX_REPLACED, BeforeModel
from runtime_api.workers import WORKER_LAUNCHED

REMOTE_RUNTIME_HOOKS_PATH: Final = "/remote-runtime/hooks/{endpoint}"
REMOTE_RUNTIME_API_PATH: Final = "/remote-runtime/api/{endpoint}"
_RENAMED_HOOKS: Final = {"sandbox": SANDBOX_REPLACED.name}


class Contract:
    """The agent API's calls as ``runtime_api`` names and answers them."""

    def request(self, name: str, body: dict[str, JsonValue]) -> tuple[str, dict[str, JsonValue]]:
        """The endpoint a call named ``name`` is, and its body as that endpoint's request."""
        return name, body

    def reply(self, endpoint: str, result: JsonValue) -> JsonValue:  # noqa: ARG002
        """``endpoint``'s answer as the caller reads it."""
        return result


class RemoteRuntimeHooks(Contract):
    """The loop's calls as MDA revisions before the agent API made them, by hook."""

    def request(self, name: str, body: dict[str, JsonValue]) -> tuple[str, dict[str, JsonValue]]:
        endpoint = _RENAMED_HOOKS.get(name, name)
        if endpoint == WORKER_LAUNCHED.name:
            # Its run_id was the launched worker's run, never the coordinator's.
            worker_run_id = body.get("run_id")
            body = {k: v for k, v in body.items() if k != "run_id"}
            body["worker_run_id"] = worker_run_id
        return endpoint, body

    def reply(self, endpoint: str, result: JsonValue) -> JsonValue:
        if endpoint != BEFORE_MODEL.name:
            return result
        # These revisions read the next model call's fields beside the phase's own.
        planned = BeforeModel.model_validate(result)
        flat = planned.model_dump(mode="json", exclude={"call"})
        if planned.call is not None:
            flat.update(planned.call.model_dump(mode="json"))
        return flat


CURRENT: Final = Contract()
REMOTE_RUNTIME_HOOKS: Final = RemoteRuntimeHooks()
