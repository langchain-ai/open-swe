"""Where the FastAPI side reaches the LangGraph server."""

import os

DEFAULT_LANGGRAPH_URL = "http://localhost:2024"


def configured_langgraph_url() -> str | None:
    """``LANGGRAPH_URL`` when set; ``LANGGRAPH_URL_PROD`` is a deprecated alias."""
    return os.environ.get("LANGGRAPH_URL") or os.environ.get("LANGGRAPH_URL_PROD") or None


def langgraph_url() -> str:
    return configured_langgraph_url() or DEFAULT_LANGGRAPH_URL
