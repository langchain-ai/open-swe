"""Encrypted sandbox environment settings and private-thread resolution."""

import re
from typing import Literal

from pydantic import BaseModel, field_validator

from openswe.encryption import decrypt_token, encrypt_token
from openswe.store import get_value, put_value

NAMESPACE = ["sandbox_environment"]


class EnvironmentUpdate(BaseModel):
    variables: dict[str, str | None]

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, variables: dict[str, str | None]) -> dict[str, str | None]:
        for name, value in variables.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Environment variable names must be valid shell identifiers")
            if name in {
                "PATH",
                "HOME",
                "LD_PRELOAD",
                "LD_LIBRARY_PATH",
                "PYTHONPATH",
                "NODE_OPTIONS",
            } or name.startswith(("OPEN_SWE_", "OPENSWE_", "GH_", "GITHUB_")):
                raise ValueError("This environment variable is reserved")
            if value is not None and ("\x00" in value or len(value) > 32768):
                raise ValueError(
                    "Environment variable values must be at most 32768 characters without NUL"
                )
        if len(variables) > 100:
            raise ValueError("At most 100 environment variables may be configured")
        return variables


async def environment_names(scope: Literal["user", "workspace"], identifier: str) -> list[str]:
    record = await get_value(NAMESPACE, f"{scope}:{identifier}") or {}
    return sorted(record)


async def save_environment(
    scope: Literal["user", "workspace"], identifier: str, update: EnvironmentUpdate
) -> list[str]:
    key = f"{scope}:{identifier}"
    record = await get_value(NAMESPACE, key) or {}
    for name, value in update.variables.items():
        if value is None:
            record.pop(name, None)
        else:
            record[name] = encrypt_token(value)
    if len(record) > 100:
        raise ValueError("At most 100 environment variables may be configured")
    await put_value(NAMESPACE, key, record)
    return sorted(record)


async def _load_environment(scope: Literal["user", "workspace"], identifier: str) -> dict[str, str]:
    record = await get_value(NAMESPACE, f"{scope}:{identifier}") or {}
    return {name: decrypt_token(value) for name, value in record.items()}


async def thread_environment(thread_id: str) -> dict[str, str]:
    from langgraph_sdk import get_client

    thread = await get_client().threads.get(thread_id)
    metadata = thread.get("metadata") or {}
    workspace = metadata.get("workspace") or metadata.get("environment") or "default"
    variables = await _load_environment("workspace", workspace)
    if metadata.get("visibility") == "private" and metadata.get("owner_login"):
        personal = await _load_environment("user", metadata["owner_login"])
        if personal:
            await get_client().threads.update(
                thread_id, metadata={"sandbox_personal_environment": True}
            )
        variables.update(personal)
    return variables
