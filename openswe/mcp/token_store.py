"""Where the MCP server's OAuth proxy keeps client registrations and tokens.

Every replica answers any OAuth step, so the state lives in the LangGraph Store,
sealed with ``TOKEN_ENCRYPTION_KEY`` because it holds upstream GitHub tokens.
"""

import math
from typing import Final, override

from key_value.aio.stores.base import BaseStore, ManagedEntry

from openswe.encryption import decrypt_token, encrypt_token
from openswe.store import delete_value, get_value, put_expiring_value, put_value

_NAMESPACE: Final = ("mcp_server_oauth",)


class SealedStore(BaseStore):
    def __init__(self) -> None:
        super().__init__(stable_api=True)

    @staticmethod
    def _namespace(collection: str) -> list[str]:
        return [*_NAMESPACE, collection]

    @override
    async def _get_managed_entry(self, *, collection: str, key: str) -> ManagedEntry | None:
        value = await get_value(self._namespace(collection), key)
        sealed = value.get("sealed") if value else None
        if not isinstance(sealed, str):
            return None
        opened = decrypt_token(sealed)
        # An entry sealed under a retired key reads as absent, so the client signs in again.
        return self._serialization_adapter.load_json(opened) if opened else None

    @override
    async def _put_managed_entry(
        self, *, collection: str, key: str, managed_entry: ManagedEntry
    ) -> None:
        namespace = self._namespace(collection)
        value = {"sealed": encrypt_token(self._serialization_adapter.dump_json(managed_entry))}
        ttl = managed_entry.ttl
        if ttl is None:
            await put_value(namespace, key, value)
        else:
            await put_expiring_value(namespace, key, value, ttl_minutes=math.ceil(ttl / 60))

    @override
    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        existed = await get_value(self._namespace(collection), key) is not None
        await delete_value(self._namespace(collection), key)
        return existed
