"""The arguments an MCP server gets for a call, as both the backend and the run send them."""

import logging
from collections.abc import Mapping
from typing import Self

from pydantic import JsonValue

logger = logging.getLogger(__name__)


class McpArguments(dict[str, JsonValue]):
    """A tool call's arguments, less the optional ones the model sent as ``""``.

    Models fill optional parameters with ``""`` rather than leave them out, and servers
    reject that where they expect an ID (Slack: ``context_channel_id=""`` is "not a
    channel ID"), costing a failed call and a retry turn. A required parameter, or one
    the schema types as other than a string, keeps its ``""`` for the server to judge.
    """

    @classmethod
    def of(cls, arguments: Mapping[str, JsonValue], schema: Mapping[str, object]) -> Self:
        """``arguments`` as the server should get them, given the tool's input ``schema``."""
        required = schema.get("required")
        properties = schema.get("properties")
        kept = cls()
        dropped: list[str] = []
        for key, value in arguments.items():
            prop = properties.get(key) if isinstance(properties, dict) else None
            if (
                value == ""
                and not (isinstance(required, list) and key in required)
                and cls._may_omit(prop)
            ):
                dropped.append(key)
            else:
                kept[key] = value
        if dropped:
            logger.debug("Leaving out empty optional MCP arguments", extra={"mcp_args": dropped})
        return kept

    @staticmethod
    def _may_omit(prop: object) -> bool:
        """Whether ``""`` for a parameter described by ``prop`` means the model skipped it:
        it is string-typed, untyped (``anyOf``, ``$ref``) or not in the schema at all."""
        if not isinstance(prop, dict):
            return True
        kind = prop.get("type")
        return kind is None or kind == "string" or (isinstance(kind, list) and "string" in kind)
