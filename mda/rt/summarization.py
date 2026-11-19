"""deepagents' summarization, sized for the model each call goes to.

deepagents summarizes a long conversation outside every middleware a graph is given,
against the model the graph was built with. A run's calls go to the model its loop
plans for each one (a route's, one a person asked for, the vision model), which may
have a smaller context window or a larger one: sized for the graph's model, the
conversation outgrows the smaller window before it is summarized, or is summarized
long before the larger one fills.
"""

from collections.abc import Awaitable, Callable

from deepagents.middleware.summarization import (
    SummarizationState,
    create_summarization_middleware,
)
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)

from rt.middleware import BackendRunMiddleware
from rt.tracing import MIDDLEWARE_TRACE_POLICY


class RunSummarizationMiddleware(AgentMiddleware):
    """Summarize as deepagents does, but measured against the model the call goes to.

    The call itself still goes on with the graph's model, which ``BackendRunMiddleware``
    swaps for the planned one as it makes the call.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    # The summary deepagents' own summarization keeps in every graph's state.
    state_schema = SummarizationState

    def __init__(self, run: BackendRunMiddleware) -> None:
        self._run = run

    @property
    def name(self) -> str:
        """deepagents replaces its own summarization with the one of the same name."""
        return "SummarizationMiddleware"

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        model = self._run.call_model(request) or request.model
        # Built for each call: the model and the run's files change from call to call,
        # and the graph is shared by concurrent runs.
        summarization = create_summarization_middleware(
            model, self._run.history_files(request.runtime)
        )

        async def as_planned(sized: ModelRequest) -> ModelResponse:
            return await handler(sized.override(model=request.model))

        return await summarization.awrap_model_call(request.override(model=model), as_planned)
