"""Backend-only workflow delivery; validation evidence still comes from GitHub."""

import asyncio
import hmac
from datetime import datetime

import httpx2
from pydantic import BaseModel, ConfigDict, SecretStr

from openswe.merge_queue.validation_contract import ValidationRequest
from openswe.utils.http_pool import pooled_transport


class DispatchEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    contract: ValidationRequest
    expires_at: datetime


class DispatchRejected(ValueError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Queue dispatch rejected ({status})")


class DispatchGateway:
    def __init__(self, url: str, secret: SecretStr):
        if len(secret.get_secret_value()) < 32:
            raise ValueError("Queue dispatch requires a signing secret of at least 32 characters")
        self.url, self.secret = url, secret

    async def dispatch(self, contract: ValidationRequest, expires_at: datetime) -> None:
        body = DispatchEnvelope(contract=contract, expires_at=expires_at).model_dump_json().encode()
        signature = hmac.digest(self.secret.get_secret_value().encode(), body, "sha256").hex()
        retry_signature = hmac.digest(
            self.secret.get_secret_value().encode(), b"retry:" + body, "sha256"
        ).hex()
        async with httpx2.AsyncClient(transport=pooled_transport(), timeout=30) as client:
            for delivery in range(3):
                response = await client.post(
                    self.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Merge-Queue-Signature": signature,
                    },
                    follow_redirects=False,
                )
                if response.status_code == 503 and hmac.compare_digest(
                    retry_signature.encode(),
                    response.headers.get("X-Merge-Queue-Retry-Signature", "").encode(),
                ):
                    if delivery == 2:
                        raise DispatchRejected(503)
                    await asyncio.sleep(1)
                    continue
                if response.status_code in {400, 401, 403, 404, 405, 409, 410, 413, 422}:
                    raise DispatchRejected(response.status_code)
                if response.status_code != 204:
                    # A proxy error may arrive after dispatch; discover the run instead of resending.
                    raise RuntimeError(f"Queue dispatch outcome unknown ({response.status_code})")
                return
