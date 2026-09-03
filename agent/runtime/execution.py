from langgraph.graph.state import RunnableConfig

from agent.run_config import RunConfig


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    return RunConfig.from_config(config).get("__is_for_execution__") is True
