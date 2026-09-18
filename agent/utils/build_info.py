"""Build identity of the running backend and its bundled dashboard.

Every value is discovered through the path that produced the artifact or
reported as ``None`` (shown as "Unavailable"): ``LANGCHAIN_REVISION_ID`` is an
opaque platform revision id, never a git SHA, and a local ``.git`` checkout
does not describe a deployed image, so neither is assumed as a source of
truth. The two artifacts are discovered independently so a mixed deployment
shows both without claiming compatibility.
"""

import functools
import json
import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from agent.config import ENV
from agent.utils.dashboard_ui import dashboard_static_dir

logger = logging.getLogger(__name__)

_BUILD_INFO_NAME = "open-swe-build-info.json"
# Image builds stamp here (scripts/stamp_build_info.py); the directory must be
# created explicitly because custom dockerfile_lines run before the source copy.
_IMAGE_BUILD_INFO_DIR = Path("/opt/open-swe-backend")


def _read_build_info(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "Ignoring unreadable build info",
            extra={"build_info_path": str(path), "build_info_error": str(exc)},
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "Ignoring build info that is not a JSON object",
            extra={"build_info_path": str(path)},
        )
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str) and value}


def _package_version() -> str | None:
    try:
        return version("open-swe-agent")
    except PackageNotFoundError:
        return None


def _backend_sidecar_path() -> Path:
    override = ENV.OPEN_SWE_BUILD_INFO_DIR.optional()
    directory = Path(override) if override else _IMAGE_BUILD_INFO_DIR
    stamped = directory / _BUILD_INFO_NAME
    # In-repo sidecar kept for local builds that stamp next to this module.
    return stamped if stamped.exists() else Path(__file__).resolve().parent / _BUILD_INFO_NAME


@functools.lru_cache(maxsize=1)
def backend_build_info() -> dict[str, str | None]:
    """Identifiers the backend knows its own running code by; values never assumed."""
    info = _read_build_info(_backend_sidecar_path())
    return {
        # Never a git SHA: LangGraph Platform assigns an opaque id per revision.
        "revision_id": ENV.LANGCHAIN_REVISION_ID.optional(),
        "commit": info.get("commit"),
        "built_at": info.get("built_at"),
        "package_version": _package_version(),
    }


@functools.lru_cache(maxsize=1)
def dashboard_build_info() -> dict[str, str | bool | None]:
    """Identifiers recorded in the dashboard bundle this backend serves, if any."""
    static = dashboard_static_dir()
    info = _read_build_info(static / _BUILD_INFO_NAME) if static else {}
    return {
        "commit": info.get("commit"),
        "built_at": info.get("built_at"),
        "served": static is not None,
    }


def build_info() -> dict[str, Any]:
    """The two artifacts' identifiers side by side; ``None`` means unavailable."""
    return {"backend": backend_build_info(), "dashboard": dashboard_build_info()}
