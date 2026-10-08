from typing import Protocol

from langchain.agents.middleware import AgentMiddleware, AgentState, TracePolicy, omit_payload

SCRUBBED_TRACE_POLICY = TracePolicy(process_inputs=omit_payload)


class TraceableMiddleware(Protocol):
    trace_policy: TracePolicy | None


class OpenSWEMiddleware[StateT: AgentState](AgentMiddleware[StateT]):
    trace_policy = SCRUBBED_TRACE_POLICY


def scrub_middleware_inputs[MiddlewareT: TraceableMiddleware](
    middleware: MiddlewareT,
) -> MiddlewareT:
    middleware.trace_policy = SCRUBBED_TRACE_POLICY
    return middleware
