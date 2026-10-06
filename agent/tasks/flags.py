from collections.abc import Mapping

from agent.users import User


async def task_coordination_enabled(login: str) -> bool:
    return bool(login) and (await User.preferences_for_login(login)).experimental_task_coordination


def task_owner_login(metadata: Mapping[str, object]) -> str:
    login = metadata.get("owner_login")
    return login if metadata.get("owner_type") == "user" and isinstance(login, str) else ""


async def require_task_coordination(metadata: Mapping[str, object]) -> None:
    if not await task_coordination_enabled(task_owner_login(metadata)):
        raise PermissionError("Asynchronous task coordination is disabled in the owner's settings")
