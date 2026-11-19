"""Validation and binding rules for caller-supplied external LangSmith sandboxes (evals)."""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Self
from urllib.parse import quote

import httpx2
from fastapi import HTTPException
from pydantic import JsonValue

from openswe.sandboxes.providers.langsmith_api import SandboxAPI
from openswe.sandboxes.this_mac import MacSandbox
from openswe.threads import index as thread_index
from openswe.threads.principals import Principal

logger = logging.getLogger(__name__)

_SANDBOX_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_WORK_DIR_LEN: Final = 1024
EXTERNAL_SANDBOX_LABEL_KEY: Final = "robin.external"
EXTERNAL_SANDBOX_LABEL_VALUE: Final = "true"


@dataclass(frozen=True, slots=True)
class ExternalSandbox:
    """An externally created LangSmith sandbox attached to a new eval thread."""

    sandbox_id: str
    work_dir: str | None = None

    @staticmethod
    def is_external(metadata: Mapping[str, object]) -> bool:
        """Whether ``metadata`` directly names an externally owned sandbox."""
        return metadata.get("sandbox_owner") == "external"

    @staticmethod
    def configured_work_dir(metadata: Mapping[str, object]) -> str | None:
        """The explicit working directory configured for an external sandbox, if any."""
        if metadata.get("sandbox_owner") != "external":
            return None
        raw = metadata.get("sandbox_work_dir")
        return raw if isinstance(raw, str) and raw else None

    @staticmethod
    async def _fetch_labels(sandbox_id: str) -> dict[str, str] | None:
        try:
            response = await SandboxAPI.from_env().request(
                "GET", f"/boxes/{quote(sandbox_id, safe='')}", timeout=10.0
            )
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        data = response.json()
        raw_labels = data.get("labels") if isinstance(data, dict) else None
        if not isinstance(raw_labels, dict):
            return {}
        return {k: v for k, v in raw_labels.items() if isinstance(k, str) and isinstance(v, str)}

    @classmethod
    async def resolve(
        cls,
        configurable: Mapping[str, object],
        principal: Principal,
        *,
        creating: bool,
    ) -> Self | None:
        """Validate and authorize an external sandbox request, or return None if not requested."""
        raw_id = configurable.get("sandbox_id")
        raw_work_dir = configurable.get("work_dir")
        if raw_id is None and raw_work_dir is None:
            return None
        if not creating:
            raise HTTPException(422, "sandbox_id can only be set when creating a thread")
        if raw_id is None:
            raise HTTPException(422, "work_dir requires sandbox_id")
        if principal.kind != "bot" and not principal.admin:
            raise HTTPException(
                403, "Only workspace admins and service keys may attach an external sandbox"
            )
        sandbox_id = cls._parse_sandbox_id(raw_id)
        work_dir = cls._parse_work_dir(raw_work_dir) if raw_work_dir is not None else None
        try:
            labels = await cls._fetch_labels(sandbox_id)
        except Exception as exc:
            logger.warning(
                "Could not verify external sandbox",
                extra={"sandbox_id": sandbox_id},
                exc_info=True,
            )
            raise HTTPException(502, "Could not verify sandbox with LangSmith") from exc
        if labels is None:
            raise HTTPException(404, f"Sandbox {sandbox_id!r} not found")
        if labels.get(EXTERNAL_SANDBOX_LABEL_KEY) != EXTERNAL_SANDBOX_LABEL_VALUE:
            raise HTTPException(
                403,
                f"Sandbox {sandbox_id!r} must carry label "
                f"{EXTERNAL_SANDBOX_LABEL_KEY}={EXTERNAL_SANDBOX_LABEL_VALUE} to attach externally",
            )
        if await thread_index.has_sandbox(sandbox_id):
            raise HTTPException(409, f"Sandbox {sandbox_id!r} is already bound to another thread")
        return cls(sandbox_id=sandbox_id, work_dir=work_dir)

    @staticmethod
    def _parse_sandbox_id(value: object) -> str:
        if not isinstance(value, str):
            raise HTTPException(422, "sandbox_id must be a string")
        sandbox_id = value.strip()
        if (
            not sandbox_id
            or MacSandbox.names(sandbox_id)
            or not _SANDBOX_ID_PATTERN.fullmatch(sandbox_id)
        ):
            raise HTTPException(422, "sandbox_id must be a valid LangSmith sandbox name")
        return sandbox_id

    @staticmethod
    def _parse_work_dir(value: object) -> str:
        if not isinstance(value, str):
            raise HTTPException(422, "work_dir must be a string")
        work_dir = value.strip()
        if (
            not work_dir
            or not work_dir.startswith("/")
            or len(work_dir) > _MAX_WORK_DIR_LEN
            or any(ch in work_dir for ch in ("\x00", "\r", "\n"))
        ):
            raise HTTPException(422, "work_dir must be an absolute path")
        return work_dir

    def metadata(self) -> dict[str, JsonValue]:
        """Thread metadata fields stamping this external sandbox."""
        data: dict[str, JsonValue] = {
            "sandbox_id": self.sandbox_id,
            "sandbox_owner": "external",
        }
        if self.work_dir is not None:
            data["sandbox_work_dir"] = self.work_dir
        return data
