"""The graphs this deployment's Agent Server serves: reads of threads from before Managed Deep Agents.

Every run plays on Managed Deep Agents, and the thread router refuses new runs on
the threads created here before that (``openswe.threads.router``). Their state,
history and streams are still read through these graphs, which hold no tools.
"""

from deepagents import create_deep_agent
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.runnables import RunnableConfig
from langgraph.pregel import Pregel

from openswe.run_config import RunConfig
from openswe.runtime.execution import bindable_config, graph_loaded_for_execution


class ReadOnlyThreadError(RuntimeError):
    """A run reached a graph that only serves reads of pre-MDA threads."""


async def read_only_graph(config: RunnableConfig) -> Pregel:
    if RunConfig.from_config(config).thread_id is not None and graph_loaded_for_execution(config):
        raise ReadOnlyThreadError(
            "This thread predates Managed Deep Agents and is read-only. "
            "Start a new thread to continue."
        )
    return create_deep_agent(
        system_prompt="",
        tools=[],
        middleware=[
            AnthropicPromptCachingMiddleware(ttl="1h", unsupported_model_behavior="ignore")
        ],
    ).with_config(bindable_config(config))
