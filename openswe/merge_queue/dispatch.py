"""Backend-only workflow delivery; validation evidence still comes from GitHub."""

import asyncio
import hmac
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid7

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from openswe.merge_queue.validation_contract import ValidationRequest
from openswe.utils.http_pool import pooled_transport


class DispatchEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    contract: ValidationRequest
    expires_at: datetime
    workflow_policy_revision: int | None = None
    workflow_digest: str | None = None


class WorkflowPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nonce: UUID
    repository: str
    repository_id: int = Field(gt=0, strict=True)
    workflow_policy_revision: int = Field(gt=0, strict=True)
    workflow_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dispatch_actor_id: int = Field(gt=0, strict=True)
    check_app_id: int = Field(gt=0, strict=True)


class WorkflowPolicyMismatch(ValueError):
    """The gateway authenticated a policy mismatch before performing any side effect."""


class DispatchRejected(ValueError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Queue dispatch rejected ({status})")


class DispatchGateway:
    def __init__(self, url: str, secret: SecretStr):
        if len(secret.get_secret_value()) < 32:
            raise ValueError("Queue dispatch requires a signing secret of at least 32 characters")
        self.url, self.secret = url, secret

    def _signature(self, body: bytes, prefix: bytes = b"") -> str:
        return hmac.digest(self.secret.get_secret_value().encode(), prefix + body, "sha256").hex()

    def _sibling_url(self, name: str) -> str:
        parts = urlsplit(self.url)
        return urlunsplit(parts._replace(path=parts.path.rsplit("/", 1)[0] + "/" + name))

    def _check_policy_mismatch(self, response: httpx2.Response, body: bytes) -> None:
        if response.status_code == 412 and hmac.compare_digest(
            self._signature(body, b"policy-mismatch:").encode(),
            response.headers.get("X-Merge-Queue-Policy-Signature", "").encode(),
        ):
            raise WorkflowPolicyMismatch(
                "Gateway workflow policy differs; retry after synchronization"
            )

    async def workflow_policy(self) -> WorkflowPolicy:
        nonce = uuid7()
        async with httpx2.AsyncClient(transport=pooled_transport(), timeout=10) as client:
            response = await client.get(
                self._sibling_url("workflow-policy"),
                params={"nonce": str(nonce)},
                headers={
                    "X-Merge-Queue-Signature": self._signature(
                        str(nonce).encode(), b"workflow-policy-request:"
                    )
                },
                follow_redirects=False,
            )
            response.raise_for_status()
        if not hmac.compare_digest(
            self._signature(response.content, b"workflow-policy-response:").encode(),
            response.headers.get("X-Merge-Queue-Signature", "").encode(),
        ):
            raise ValueError("Unauthenticated workflow policy")
        policy = WorkflowPolicy.model_validate_json(response.content)
        if policy.nonce != nonce:
            raise ValueError("Workflow policy response belongs to a different request")
        return policy

    async def report(
        self,
        contract: ValidationRequest,
        expires_at: datetime,
        state: Literal["in_progress", "recovering", "cancelled", "success"],
        *,
        run_id: int | None = None,
        workflow_policy_revision: int | None = None,
        workflow_digest: str | None = None,
    ) -> None:
        envelope = DispatchEnvelope(
            contract=contract,
            expires_at=expires_at,
            workflow_policy_revision=workflow_policy_revision,
            workflow_digest=workflow_digest,
        )
        report = CheckReport(
            dispatch_body=envelope.model_dump_json(exclude_none=True), state=state, run_id=run_id
        )
        body = report.model_dump_json(exclude_none=True).encode()
        signature = self._signature(body, b"check-report:")
        url = self._sibling_url("check-reports")
        async with httpx2.AsyncClient(transport=pooled_transport(), timeout=30) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Merge-Queue-Signature": signature,
                },
                follow_redirects=False,
            )
            self._check_policy_mismatch(response, body)
            response.raise_for_status()

    async def dispatch(
        self,
        contract: ValidationRequest,
        expires_at: datetime,
        *,
        workflow_policy_revision: int | None = None,
        workflow_digest: str | None = None,
    ) -> None:
        body = (
            DispatchEnvelope(
                contract=contract,
                expires_at=expires_at,
                workflow_policy_revision=workflow_policy_revision,
                workflow_digest=workflow_digest,
            )
            .model_dump_json(exclude_none=True)
            .encode()
        )
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
                self._check_policy_mismatch(response, body)
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


class CheckReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dispatch_body: str
    state: Literal["in_progress", "recovering", "cancelled", "success"]
    run_id: int | None = None
