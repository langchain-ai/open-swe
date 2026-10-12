"""Discover agent-manageable feature flags from settings schemas."""

from pydantic import BaseModel


def feature_flag_names(model: type[BaseModel]) -> frozenset[str]:
    """Return only boolean fields explicitly marked as agent-manageable flags."""
    return frozenset(
        name
        for name, field in model.model_fields.items()
        if isinstance(field.json_schema_extra, dict)
        and field.json_schema_extra.get("agent_feature_flag") is True
    )
