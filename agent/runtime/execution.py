from langgraph.graph.state import RunnableConfig


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    configurable = config.get("configurable")
    return bool(isinstance(configurable, dict) and configurable.get("__is_for_execution__", False))
