"""Slack model selector shown on ordinary agent replies."""

from agent.dashboard.options import SUPPORTED_MODELS
from agent.run_config import RunConfig
from agent.utils.json_types import JsonObject

MODEL_SELECT_ACTION_ID = "open_swe_model_select"


def selectable_models() -> list[tuple[str, str, str]]:
    return [
        (model["id"], model["label"], model["default_effort"])
        for model in SUPPORTED_MODELS
        if model.get("can_be_default", True)
    ]


def model_selection(model_id: str) -> tuple[str, str, str] | None:
    return next((model for model in selectable_models() if model[0] == model_id), None)


def model_selector_block(cfg: RunConfig) -> JsonObject | None:
    selected = model_selection(cfg.resolved_agent_model_id or cfg.agent_model_id or "")
    if selected is None:
        return None
    options = [
        {
            "text": {"type": "plain_text", "text": label, "emoji": True},
            "value": model_id,
        }
        for model_id, label, _effort in selectable_models()
    ]
    return {
        "type": "actions",
        "elements": [
            {
                "type": "static_select",
                "action_id": MODEL_SELECT_ACTION_ID,
                "placeholder": {"type": "plain_text", "text": "Choose model", "emoji": True},
                "options": options,
                "initial_option": next(
                    option for option in options if option["value"] == selected[0]
                ),
            }
        ],
    }


def selected_model_id(selected_option: object) -> str | None:
    if not isinstance(selected_option, dict):
        return None
    value = selected_option.get("value")
    return value if isinstance(value, str) and model_selection(value) is not None else None
