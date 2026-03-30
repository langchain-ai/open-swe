import os

from langchain.chat_models import init_chat_model

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"
_OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_model_id(model_id: str) -> str:
    if ":" in model_id:
        return model_id

    if model_id.startswith(_OPENAI_MODEL_PREFIXES):
        return f"openai:{model_id}"

    return model_id


def make_model(model_id: str, **kwargs: dict):
    normalized_model_id = _normalize_model_id(model_id)
    model_kwargs = kwargs.copy()

    if normalized_model_id.startswith("openai:"):
        openai_base_url = os.environ.get("OPENAI_API_BASE", "").strip()
        use_responses_api = _env_flag("OPENAI_USE_RESPONSES_API", default=True)
        openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()

        if openai_base_url:
            model_kwargs.setdefault("base_url", openai_base_url)
        elif use_responses_api:
            model_kwargs.setdefault("base_url", OPENAI_RESPONSES_WS_BASE_URL)

        model_kwargs.setdefault("use_responses_api", use_responses_api)

        if openai_api_key:
            model_kwargs.setdefault("api_key", openai_api_key)

    return init_chat_model(model=normalized_model_id, **model_kwargs)
