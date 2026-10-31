"""Tools: ``preview_app`` and ``publish_app``. Run an app built in the sandbox in the web UI."""

import logging
from typing import Any

from langgraph_sdk import get_client

from openswe.plugins.manifest import BundleError
from openswe.plugins.records import AppVersion, PluginApps, PluginInstallations, PluginNameTaken
from openswe.run_config import RunConfig
from openswe.sandboxes.state import get_sandbox_backend
from openswe.threads.summary import APP_PREVIEW_KEY
from openswe.users.models import User
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class _AppBuild:
    """The bundle at ``path`` in this run's sandbox, published for the person the thread is for."""

    def __init__(self, path: str) -> None:
        self.path = path.strip()
        cfg = RunConfig.from_runtime()
        if not cfg.thread_id or not cfg.github_login:
            raise ValueError("apps are built in a person's own thread")
        self.thread_id = str(cfg.thread_id)
        self.login = cfg.github_login

    async def person(self) -> User:
        user = await User.for_login("github", self.login)
        if user is None:
            raise ValueError(f"{self.login} has no Open SWE account yet")
        return user

    async def html(self) -> str:
        if not self.path.endswith(".html"):
            raise ValueError("bundle_path must be the built .html file")
        backend = await get_sandbox_backend(self.thread_id)
        [download] = await backend.adownload_files([self.path])
        if download.content is None:
            raise ValueError(f"could not read {self.path}: {download.error}")
        try:
            return download.content.decode()
        except UnicodeDecodeError as exc:
            raise ValueError("the bundle must be UTF-8 HTML") from exc

    async def publish(self, *, preview: bool) -> tuple[User, AppVersion]:
        user = await self.person()
        origin: JsonObject = {"thread": self.thread_id, "path": self.path}
        version = await PluginApps.publish(
            user.id,
            await self.html(),
            origin=origin,
            preview_of=self.thread_id if preview else None,
        )
        return user, version


def _failed(exc: Exception) -> dict[str, Any]:
    return {"success": False, "error": str(exc)}


async def preview_app(bundle_path: str) -> dict[str, Any]:
    """Implement the `preview_app` tool."""
    try:
        build = _AppBuild(bundle_path)
        user, version = await build.publish(preview=True)
        running = await PluginInstallations.preview(build.thread_id, version, user_id=user.id)
        await get_client().threads.update(build.thread_id, metadata={APP_PREVIEW_KEY: True})
    except (ValueError, BundleError) as exc:
        return _failed(exc)
    if not running:
        return {
            "success": True,
            "previewing": False,
            "waiting_for": "the person to approve the permissions it asks for",
            "permissions": list(version.manifest.permissions),
        }
    return {"success": True, "previewing": True, "app": version.manifest.name}


async def publish_app(bundle_path: str) -> dict[str, Any]:
    """Implement the `publish_app` tool."""
    try:
        build = _AppBuild(bundle_path)
        user, version = await build.publish(preview=False)
    except (ValueError, BundleError) as exc:
        return _failed(exc)
    # What the person already approved for this app: its preview here, or their install.
    installed = next(
        (
            installation
            for installation in await PluginInstallations.visible_to(user.id)
            if installation.plugin == version.app
        ),
        None,
    )
    preview = await PluginInstallations.previewing(build.thread_id, user.id)
    approved = {
        *(installed.manifest.permissions if installed else ()),
        *(preview.manifest.permissions if preview and preview.plugin == version.app else ()),
    }
    result: dict[str, Any] = {
        "success": True,
        "app": version.manifest.name,
        "number": version.number,
    }
    if not set(version.manifest.permissions) <= approved:
        result["installed"] = False
        result["next"] = "the person adds it, approving its permissions, from Settings → Apps"
        return result
    try:
        if installed is None:
            await PluginInstallations.install(version, user_id=user.id)
        else:
            await PluginInstallations.upgrade(installed.id, version, by=user.id)
    except PluginNameTaken as exc:
        result["installed"] = False
        result["next"] = str(exc)
        return result
    result["installed"] = True
    return result
