"""A requested offload: the conversation summarized now, whatever its length.

MDA's own summarization offloads a long conversation on its own. A person may ask for
it sooner, from the dashboard; the run started for that summarizes the conversation as
MDA's summarization would and ends. The event it writes is the one MDA's summarization
applies to every later model call.
"""

from typing import Final

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.summarization import (
    SUMMARIZATION_EVENT_KEY,
    SummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from pydantic import JsonValue

# Held state the transcript reads the offload's outcome from, with the next model call.
OFFLOADING_KEY: Final = "conversation_offloading"


class RequestedOffload(SummarizationMiddleware):
    """MDA's summarization, made to summarize the conversation now."""

    def __init__(self, model: BaseChatModel, backend: BackendProtocol) -> None:
        # Its summary is the run's work, not something to stream to the person.
        summary_model = model.model_copy(
            update={"tags": [*(model.tags or []), "nostream", "langsmith:hidden"]}
        )
        super().__init__(
            model=summary_model, backend=backend, **compute_summarization_defaults(model)
        )

    def _status(self, status: str, **details: JsonValue) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"status": status, "trigger": "manual", **details}
        get_stream_writer()({"type": OFFLOADING_KEY, **payload})
        return payload

    def _should_summarize(self, messages: list[AnyMessage], total_tokens: int) -> bool:  # noqa: ARG002
        return True

    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        cutoff = super()._determine_cutoff_index(messages)
        # Nothing but earlier summaries before the cutoff: there is nothing new to offload.
        return cutoff if self._filter_summary_messages(messages[:cutoff]) else 0

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        self._status("started")
        try:
            return await super()._acreate_summary(messages_to_summarize)
        except Exception:
            self._status("failed")
            raise

    async def offload(
        self, state: AgentState, runtime: Runtime
    ) -> tuple[dict[str, object], dict[str, JsonValue]]:
        """The conversation summarized: the graph state it writes, and the offload's outcome."""

        async def finish(_request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=[])

        response = await self.awrap_model_call(
            ModelRequest(
                model=self.model,
                messages=state.get("messages", []),
                tools=[],
                state=state,
                runtime=runtime,
            ),
            finish,
        )
        update = (
            response.command.update
            if isinstance(response, ExtendedModelResponse) and response.command
            else None
        )
        event = update.get(SUMMARIZATION_EVENT_KEY) if isinstance(update, dict) else None
        if not isinstance(update, dict) or not isinstance(event, dict):
            return {}, self._status("skipped")
        return update, self._status(
            "completed", cutoff_index=event["cutoff_index"], file_path=event["file_path"]
        )
