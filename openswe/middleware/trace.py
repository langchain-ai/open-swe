from langchain.agents.middleware import AgentMiddleware, AgentState, TracePolicy, omit_payload

SCRUBBED_TRACE_POLICY = TracePolicy(process_inputs=omit_payload)


class OpenSWEMiddleware[StateT: AgentState](AgentMiddleware[StateT]):
    trace_policy = SCRUBBED_TRACE_POLICY
