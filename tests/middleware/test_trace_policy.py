import inspect

from langchain.agents.middleware import AgentMiddleware, omit_payload

from agent import middleware


def test_custom_middleware_scrubs_trace_inputs() -> None:
    exports = [getattr(middleware, name) for name in middleware.__all__]
    custom_middleware = [
        value
        for value in exports
        if isinstance(value, AgentMiddleware)
        or (inspect.isclass(value) and issubclass(value, AgentMiddleware))
    ]

    assert custom_middleware
    for value in custom_middleware:
        assert value.trace_policy is not None
        assert value.trace_policy.process_inputs is omit_payload
