"""LangSmith API keys as Robin credentials.

Robin mints no keys of its own. A caller presents a LangSmith key and LangSmith
says whose it is: Robin calls the tenant ``/auth`` endpoint as the key, pinned to
the workspace Robin signs people in to (the one its own ``LANGSMITH_API_KEY``
belongs to). A key that cannot act in that workspace is refused, whatever else it
can reach.

- A personal access token (``lsv2_pt_``) is a person: the Robin user who signed
  in with that LangSmith account.
- A service key (``lsv2_sk_``) is the workspace's bot, but only one that holds
  ``workspaces:manage`` there (a Workspace Admin key). The bot can start threads
  in any repository the GitHub App reaches, so a key made for tracing or CI must
  not become it just by existing in a large shared workspace.

Every other LangSmith key (tracing keys, legacy ``ls__`` keys) is refused, and so
are Robin's own deployment keys: whatever can read Robin's environment must not
be able to act as its bot.
"""

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Self

import httpx2
from fastapi import HTTPException

from openswe.config import ENV
from openswe.dashboard.langsmith_login import LANGSMITH_ORG_DISCOVERY
from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)

PERSONAL_ACCESS_TOKEN_PREFIX = "lsv2_pt_"
SERVICE_KEY_PREFIX = "lsv2_sk_"
BOT_PERMISSION = "workspaces:manage"
# How long LangSmith's answer for a key is reused. A key revoked in LangSmith, or
# one whose role lost BOT_PERMISSION, keeps working here for at most this long.
VERIFIED_TTL_SECONDS = 60.0
MAX_VERIFIED = 1024

INVALID_KEY = "invalid LangSmith API key"
SERVICE_KEY_NOT_A_BOT = (
    f"This LangSmith service key needs the {BOT_PERMISSION} permission in Robin's "
    "LangSmith workspace (a Workspace Admin key) to act as Robin's bot"
)
DEPLOYMENT_KEY = "Robin's own LangSmith keys cannot authenticate to its API"

LangSmithKeyKind = Literal["personal", "service"]


@dataclass(frozen=True, kw_only=True)
class LangSmithApiKey:
    """A LangSmith key LangSmith vouched for in Robin's workspace. Never the secret."""

    kind: LangSmithKeyKind
    # A digest prefix that names the key in logs.
    fingerprint: str
    organization_id: str
    workspace_id: str
    # LangSmith's id for the key, for audit entries; the fingerprint when it gave none.
    audit_id: str
    ls_user_id: str = ""
    email: str = ""

    @staticmethod
    def kind_of(token: str) -> LangSmithKeyKind | None:
        """Which kind of LangSmith key ``token`` claims to be, or ``None`` for anything else."""
        if token.startswith(PERSONAL_ACCESS_TOKEN_PREFIX):
            return "personal"
        if token.startswith(SERVICE_KEY_PREFIX):
            return "service"
        return None

    @staticmethod
    def rejected() -> HTTPException:
        return HTTPException(401, INVALID_KEY, headers={"WWW-Authenticate": "Bearer"})

    @classmethod
    def from_auth(
        cls,
        data: dict[str, object],
        *,
        kind: LangSmithKeyKind,
        fingerprint: str,
        organization_id: str,
        workspace_id: str,
    ) -> Self:
        """The key LangSmith's ``/auth`` answer describes, refused unless it is Robin's to accept."""

        def text(field: str) -> str:
            value = data.get(field)
            return value.strip() if isinstance(value, str) else ""

        log = {"api_key_fingerprint": fingerprint, "api_key_kind": kind}
        if text("tenant_id") != workspace_id or text("organization_id") != organization_id:
            logger.info("Refused a LangSmith API key from another workspace", extra=log)
            raise cls.rejected()
        ls_user_id = text("ls_user_id")
        # LangSmith names a person only for a personal access token; a mismatch means
        # the prefix lied about what the key is.
        if (kind == "personal") != bool(ls_user_id):
            logger.warning("LangSmith API key identity does not match its kind", extra=log)
            raise cls.rejected()
        if kind == "service":
            permissions = data.get("identity_permissions")
            if not isinstance(permissions, list) or BOT_PERMISSION not in permissions:
                logger.info("Refused a LangSmith service key without bot permission", extra=log)
                raise HTTPException(403, SERVICE_KEY_NOT_A_BOT)
        api_key_id = text("api_key_id")
        return cls(
            kind=kind,
            fingerprint=fingerprint,
            organization_id=organization_id,
            workspace_id=workspace_id,
            audit_id=f"langsmith:{api_key_id or fingerprint}",
            ls_user_id=ls_user_id,
            email=text("user_email"),
        )


class LangSmithApiKeys:
    """Verifies presented LangSmith keys with LangSmith, keeping its recent yeses.

    Refusals are never kept, so the cache only ever holds keys LangSmith accepted
    and stays bounded by real callers rather than by whoever sends garbage.
    """

    def __init__(self) -> None:
        self.verified: OrderedDict[str, tuple[LangSmithApiKey, float]] = OrderedDict()

    def clear(self) -> None:
        self.verified.clear()

    async def verify(self, token: str) -> LangSmithApiKey | None:
        """The LangSmith key ``token`` is, checked against Robin's workspace.

        ``None`` when ``token`` is not a personal access token or service key at all,
        so a route that accepts other bearer credentials can try them. A key LangSmith
        refuses, or one from another workspace, raises 401; a service key that may not
        be the bot, or one of Robin's own keys, raises 403; LangSmith being unreachable
        raises 503.
        """
        token = token.strip()
        kind = LangSmithApiKey.kind_of(token)
        if kind is None:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        if digest in self.deployment_digests():
            raise HTTPException(403, DEPLOYMENT_KEY)
        organization_id, workspace_id = await LANGSMITH_ORG_DISCOVERY.resolve()
        if (cached := self.cached(digest, workspace_id)) is not None:
            return cached
        key = LangSmithApiKey.from_auth(
            await self.auth(token, workspace_id),
            kind=kind,
            fingerprint=digest[:16],
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        self.remember(digest, key)
        return key

    @staticmethod
    def deployment_digests() -> frozenset[str]:
        keys = (
            ENV.LANGSMITH_API_KEY.get(),
            ENV.LANGSMITH_GATEWAY_API_KEY.get(),
            ENV.MDA_RUNTIME_API_KEY.get(),
        )
        return frozenset(hashlib.sha256(key.strip().encode()).hexdigest() for key in keys if key)

    def cached(self, digest: str, workspace_id: str) -> LangSmithApiKey | None:
        entry = self.verified.get(digest)
        if entry is None:
            return None
        key, expires_at = entry
        if expires_at <= monotonic() or key.workspace_id != workspace_id:
            self.verified.pop(digest, None)
            return None
        return key

    def remember(self, digest: str, key: LangSmithApiKey) -> None:
        self.verified[digest] = (key, monotonic() + VERIFIED_TTL_SECONDS)
        self.verified.move_to_end(digest)
        while len(self.verified) > MAX_VERIFIED:
            self.verified.popitem(last=False)

    @staticmethod
    async def auth(token: str, workspace_id: str) -> dict[str, object]:
        """LangSmith's tenant ``/auth`` answer for ``token`` acting in ``workspace_id``.

        ``/auth`` rather than ``/auth/public``: only it reports the key's workspace
        permissions and id, which the bot check and audit entries need.
        """
        endpoint = ENV.LANGSMITH_ENDPOINT.get().rstrip("/")
        try:
            async with httpx2.AsyncClient(
                timeout=httpx2.Timeout(5.0, connect=2.0), transport=pooled_transport()
            ) as client:
                response = await client.get(
                    endpoint + "/auth",
                    headers={
                        "Accept": "application/json",
                        "X-Api-Key": token,
                        "X-Tenant-Id": workspace_id,
                    },
                    follow_redirects=False,
                )
        except httpx2.HTTPError as exc:
            raise HTTPException(503, "LangSmith could not verify the API key") from exc
        if response.status_code in {401, 403}:
            raise LangSmithApiKey.rejected()
        if not response.is_success:
            raise HTTPException(503, "LangSmith could not verify the API key")
        try:
            data = response.json()
        except ValueError as exc:
            raise HTTPException(503, "LangSmith returned an unreadable key verification") from exc
        if not isinstance(data, dict):
            raise HTTPException(503, "LangSmith returned an unreadable key verification")
        return data


LANGSMITH_API_KEYS = LangSmithApiKeys()
