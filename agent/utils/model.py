import os

from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_MODELS = ["MiniMax-M2.7", "MiniMax-M2.7-highspeed"]


def make_model(model_id: str, **kwargs: dict):
    model_kwargs = kwargs.copy()

    if model_id.startswith("openai:"):
        model_kwargs["base_url"] = OPENAI_RESPONSES_WS_BASE_URL
        model_kwargs["use_responses_api"] = True
    elif model_id.startswith("minimax:"):
        minimax_model = model_id[len("minimax:"):]
        # MiniMax temperature range is (0.0, 1.0]; clamp 0 to the default 1.0
        if model_kwargs.get("temperature") == 0:
            model_kwargs["temperature"] = 1.0
        return ChatOpenAI(
            model=minimax_model,
            base_url=MINIMAX_BASE_URL,
            api_key=os.environ.get("MINIMAX_API_KEY", ""),
            **model_kwargs,
        )

    return init_chat_model(model=model_id, **model_kwargs)
