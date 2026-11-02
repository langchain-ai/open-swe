from openswe.graphs.read_only import read_only_graph

# langgraph.json entrypoint: reads of threads from before Managed Deep Agents.
traced_agent = read_only_graph

__all__ = ["traced_agent"]
