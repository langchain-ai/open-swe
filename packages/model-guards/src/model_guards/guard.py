from langchain.agents.middleware import AgentMiddleware, AgentState, TracePolicy, omit_payload


class ModelGuard[StateT: AgentState](AgentMiddleware[StateT]):
    """Model-call middleware whose traces leave out the request, which carries the conversation."""

    trace_policy = TracePolicy(process_inputs=omit_payload)


def underlying[ModelT](model: object, model_type: type[ModelT]) -> ModelT | None:
    """``model``, or the chat model it binds, when that is a ``model_type``."""
    current = model
    # Bindings nest a few deep at most; the bound stops a self-referencing chain.
    for _ in range(10):
        if isinstance(current, model_type):
            return current
        bound = getattr(current, "bound", None)
        if bound is None or bound is current:
            return None
        current = bound
    return None
