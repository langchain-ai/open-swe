"""Which sandbox carries whose secrets: only a private thread's, and only its owner's."""

import logging
from uuid import UUID

from langgraph_sdk import get_client
from pydantic import ValidationError

from openswe.sandbox_secrets.models import SandboxSecret, SecretView
from openswe.tasks.schemas import ThreadMetadata, user_for_login

logger = logging.getLogger(__name__)


async def _metadata(thread_id: str) -> ThreadMetadata | None:
    thread = await get_client().threads.get(thread_id)
    try:
        return ThreadMetadata.model_validate(thread.get("metadata") or {})
    except ValidationError:
        logger.warning("Unrecognized thread metadata for sandbox secrets", exc_info=True)
        return None


async def _private_owner(metadata: ThreadMetadata | None) -> UUID | None:
    if metadata is None or metadata.visibility != "private" or metadata.owner_type == "system":
        return None
    if metadata.owner_user_id is not None:
        return metadata.owner_user_id
    user = await user_for_login(metadata.owner_login) if metadata.owner_login else None
    return user.id if user is not None else None


async def thread_secret_rules(thread_id: str) -> list[dict[str, object]]:
    """Proxy rules for the secrets of the person who privately owns this thread's sandbox.

    A sandbox shared with another thread carries them only while both threads are
    private to the same person, so a public guest strips them from the box.
    """
    metadata = await _metadata(thread_id)
    owner = await _private_owner(metadata)
    host_id = metadata.sandbox_host_thread_id if metadata is not None else None
    if owner is None:
        return []
    if host_id and host_id != thread_id and await _private_owner(await _metadata(host_id)) != owner:
        return []
    rules: list[dict[str, object]] = []
    for secret in await SandboxSecret.owned_by(owner):
        rule = secret.proxy_rule()
        if rule is None:
            logger.warning(
                "Skipping a sandbox secret that could not be decrypted",
                extra={"secret_name": secret.name, "owner_user_id": str(owner)},
            )
            continue
        rules.append(rule)
    return rules


async def secrets_for_login(login: str | None) -> list[SecretView]:
    """The secrets a run acting for ``login`` in their private thread can use, without values."""
    user = await user_for_login(login) if login else None
    if user is None:
        return []
    return [secret.view() for secret in await SandboxSecret.owned_by(user.id)]
