"""Analytics opt-out switches."""

from __future__ import annotations

from collections.abc import Mapping

TELEMETRY_DISABLED_ENV = "MDA_TELEMETRY_DISABLED"
DO_NOT_TRACK_ENV = "DO_NOT_TRACK"


def is_truthy_env(value: str) -> bool:
    """Must match ``is_truthy_env`` in ``crates/mda/src/telemetry/gates.rs``."""
    return value.strip().lower() not in {"", "0", "false"}


def is_telemetry_disabled(env: Mapping[str, str]) -> bool:
    return any(
        is_truthy_env(value)
        for value in (env.get(TELEMETRY_DISABLED_ENV), env.get(DO_NOT_TRACK_ENV))
        if value is not None
    )
