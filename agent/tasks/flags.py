from collections.abc import Mapping

from agent.bridge.constants import SANDBOX_ID_PREFIX
from agent.users import User


async def task_coordination_enabled(login: str) -> bool:
    return bool(login) and (await User.preferences_for_login(login)).experimental_task_coordination


def task_owner_login(metadata: Mapping[str, object]) -> str:
    login = metadata.get("owner_login")
    return login if metadata.get("owner_type") == "user" and isinstance(login, str) else ""


def task_coordination_supported(metadata: Mapping[str, object]) -> bool:
    sandbox_id = metadata.get("sandbox_id")
    if isinstance(sandbox_id, str) and sandbox_id.startswith(SANDBOX_ID_PREFIX):
        # CLI bridges close when the coordinator's run ends, before workers finish.
        return metadata.get("sandbox_bridge_client") == "desktop"
    return True


async def require_task_coordination(metadata: Mapping[str, object]) -> None:
    if not task_coordination_supported(metadata):
        raise ValueError("Asynchronous task coordination is unavailable on a one-shot CLI bridge")
    if not await task_coordination_enabled(task_owner_login(metadata)):
        raise PermissionError("Asynchronous task coordination is disabled in the owner's settings")
