import os

from langchain.chat_models import init_chat_model

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"

MINIMAX_BASE_URL = "https://api.minimax.io/v1"

# MiniMax models and their context window sizes
MINIMAX_MODELS = {
    "MiniMax-M2.5": 204_000,
    "MiniMax-M2.5-highspeed": 204_000,
}


def make_model(model_id: str, **kwargs: dict):
    model_kwargs = kwargs.copy()

    if model_id.startswith("openai:"):
        model_kwargs["base_url"] = OPENAI_RESPONSES_WS_BASE_URL
        model_kwargs["use_responses_api"] = True
    elif model_id.startswith("minimax:"):
        # MiniMax uses an OpenAI-compatible API, so we route through the
        # openai provider with a custom base_url and api_key.
        model_name = model_id.split(":", 1)[1]
        # Clamp temperature to MiniMax's supported range [0.0, 1.0]
        if "temperature" in model_kwargs:
            model_kwargs["temperature"] = min(max(model_kwargs["temperature"], 0.0), 1.0)
        model_kwargs["base_url"] = MINIMAX_BASE_URL
        model_kwargs["api_key"] = os.environ.get("MINIMAX_API_KEY", "")
        model_id = f"openai:{model_name}"

    return init_chat_model(model=model_id, **model_kwargs)
