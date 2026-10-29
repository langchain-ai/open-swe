"""The calls that make an async subagent the runtime launches an Open SWE task worker.

The coordinator's run asks for a worker grant (``launch_worker``), launches the
worker with it in place of its own run token, then registers the thread it
launched (``worker_launched``). The worker's first run trades the grant for a run
token of its own (``adopt``), and the catalog planned for it; a grant may also read
its coordinator's catalog, and nothing else.
"""

from pydantic import BaseModel, JsonValue

from runtime_api.catalog import Catalog
from runtime_api.endpoint import LOOP_CALL_SECONDS, Endpoint


class Launch(BaseModel):
    # The coordinator's ``start_async_task`` call, which a launch is idempotent per.
    tool_call_id: str
    instructions: str


class LaunchReply(BaseModel):
    """A grant and the assignment to launch with, or the worker a replayed launch made."""

    worker: dict[str, JsonValue] | None = None
    grant: str | None = None
    assignment: str | None = None


class Launched(Launch):
    worker_thread_id: str
    worker_run_id: str


class Worker(BaseModel):
    """The worker registered under the coordinator's task, as its tool call answers it."""

    worker: dict[str, JsonValue]


class Adopt(BaseModel):
    thread_id: str
    run_id: str | None = None
    # The assignment the worker was launched with, its first message.
    instructions: str


class Adopted(BaseModel):
    run_token: str
    # The worker's own catalog.
    catalog: Catalog
    # The worker run's invocation, which its usage counts its replies by.
    invocation_id: str | None = None


# Only authorizes a launch, or returns the worker it already made.
LAUNCH_WORKER = Endpoint(
    "launch_worker", Launch, LaunchReply, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
WORKER_LAUNCHED = Endpoint("worker_launched", Launched, Worker, seconds=LOOP_CALL_SECONDS)
ADOPT = Endpoint("adopt", Adopt, Adopted, seconds=LOOP_CALL_SECONDS)
