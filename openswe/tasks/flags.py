from collections.abc import Mapping
from uuid import UUID

from openswe.bridge.constants import SANDBOX_ID_PREFIX
from openswe.tasks.schemas import ThreadMetadata


async def task_coordination_enabled(login: str, *, owner_user_id: UUID | None = None) -> bool:
    return False


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
    owner = ThreadMetadata.model_validate(metadata)
    if owner.owner_type != "user" or not await task_coordination_enabled(
        owner.owner_login or "", owner_user_id=owner.owner_user_id
    ):
        raise PermissionError("Asynchronous task coordination is disabled in the owner's settings")
