"""Response and item ids that name the Open SWE thread they came from.

A client that resends its whole history instead of ``previous_response_id``
still echoes these ids back, which is how a stateless request finds its thread.
"""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

type ItemKind = Literal["msg", "mcp", "ws", "fc", "ctc"]

_UUID_HEX = 32
_SUFFIX_HEX = 16


@dataclass(frozen=True, slots=True)
class OpenSweId:
    thread_id: str
    run_id: str | None = None

    @classmethod
    def parse(cls, value: str) -> OpenSweId | None:
        prefix, _, body = value.partition("_")
        if prefix not in {"resp", "msg", "mcp", "ws", "fc", "ctc"} or len(body) < _UUID_HEX:
            return None
        try:
            thread_id = str(uuid.UUID(hex=body[:_UUID_HEX]))
            run_id = (
                str(uuid.UUID(hex=body[_UUID_HEX : 2 * _UUID_HEX]))
                if prefix == "resp" and len(body) == 2 * _UUID_HEX
                else None
            )
        except ValueError:
            return None
        if prefix == "resp" and run_id is None:
            return None
        return cls(thread_id=thread_id, run_id=run_id)

    def response_id(self) -> str:
        if self.run_id is None:
            raise ValueError("a response id needs a run id")
        return f"resp_{uuid.UUID(self.thread_id).hex}{uuid.UUID(self.run_id).hex}"

    def item_id(self, kind: ItemKind, source_id: str) -> str:
        suffix = hashlib.sha256(source_id.encode()).hexdigest()[:_SUFFIX_HEX]
        return f"{kind}_{uuid.UUID(self.thread_id).hex}{suffix}"
