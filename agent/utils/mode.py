from langgraph.config import get_config


def is_eval_mode() -> bool:
    """Check if the current run is in eval mode."""
    config = get_config()
    return config.get("configurable", {}).get("mode") == "eval"
