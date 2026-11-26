"""Request-scoped dashboard authentication through Robin or LangSmith credentials."""

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from openswe.config import ENV
from openswe.dashboard.langsmith_api_keys import LangSmithApiKeys
from openswe.dashboard.langsmith_login import (
    LANGSMITH_ORG_DISCOVERY,
    LangSmithAccount,
    LangSmithLogin,
)
from openswe.dashboard.oauth import COOKIE_NAME, decode_session
from openswe.dashboard.principal import AuthorizedClaims, DashboardPrincipal
from openswe.database import postgres
from openswe.users import User
from openswe.utils.http_pool import pooled_transport


class LangSmithRequestIdentity(BaseModel):
    organization_id: str
    tenant_id: str
    ls_user_id: str = ""
    user_email: str = ""
    service_identity: str = ""
    is_code_evaluator_jwt: bool = False


class DashboardAuthentication:
    def __init__(self, request: HTTPConnection) -> None:
        self.request = request

    async def langsmith_claims(self) -> AuthorizedClaims:
        """Authenticate credentials through the trusted LangSmith endpoint."""
        headers = {
            name: value
            for name in ("authorization", "x-api-key", "x-service-key")
            if (value := self.request.headers.get(name))
        }
        if not headers:
            value = self.request.cookies.get("_ls_oauth_user", "")
            if not value.strip():
                raise HTTPException(401, "not authenticated")
            cookie = SimpleCookie()
            cookie["_ls_oauth_user"] = value
            headers["cookie"] = cookie.output(header="").strip()
        for value in headers.values():
            scheme, _, credential = value.partition(" ")
            token = credential.strip() if scheme.lower() == "bearer" else value.strip()
            if hashlib.sha256(token.encode()).hexdigest() in LangSmithApiKeys.deployment_digests():
                raise HTTPException(
                    403, "Robin's own LangSmith keys cannot authenticate to its API"
                )
        organization_id, workspace_id = await LANGSMITH_ORG_DISCOVERY.resolve()
        tenant = self.request.headers.get("x-tenant-id")
        if tenant is not None and tenant != workspace_id:
            raise HTTPException(403, "Your LangSmith workspace is not authorized")
        headers.update({"X-Tenant-Id": workspace_id, "Accept": "application/json"})
        try:
            async with httpx2.AsyncClient(
                timeout=httpx2.Timeout(5.0, connect=2.0), transport=pooled_transport()
            ) as client:
                response = await client.get(
                    ENV.LANGSMITH_ENDPOINT.get().rstrip("/") + "/auth",
                    headers=headers,
                    follow_redirects=False,
                )
        except httpx2.HTTPError as exc:
            raise HTTPException(503, "LangSmith authentication is temporarily unavailable") from exc
        if response.status_code in {401, 403}:
            raise HTTPException(response.status_code, "LangSmith authentication failed")
        if not response.is_success:
            raise HTTPException(503, "LangSmith authentication is temporarily unavailable")
        try:
            identity = LangSmithRequestIdentity.model_validate_json(response.content)
        except ValidationError as exc:
            raise HTTPException(502, "Invalid LangSmith authentication response") from exc
        if identity.organization_id != organization_id or identity.tenant_id != workspace_id:
            raise HTTPException(403, "Your LangSmith workspace is not authorized")
        if (
            identity.service_identity
            or identity.is_code_evaluator_jwt
            or not identity.ls_user_id.strip()
        ):
            raise HTTPException(403, "This operation requires a LangSmith user")
        user = await User.for_identity("langsmith", identity.ls_user_id)
        if user is None:
            user = await User.sign_in(
                "langsmith",
                identity.ls_user_id,
                email=identity.user_email,
                authorization=LangSmithAccount(
                    sub=identity.ls_user_id, ls_org_id=identity.organization_id
                ),
            )
        return AuthorizedClaims(
            {
                "provider": "langsmith",
                "sub": user.session_login,
                "user_id": str(user.id),
                "langsmith_subject": identity.ls_user_id,
                "email": identity.user_email or user.email,
                "avatar_url": user.avatar_url,
            },
            user,
        )

    @asynccontextmanager
    async def authorized_person(self) -> AsyncGenerator[tuple[AsyncSession, AuthorizedClaims]]:
        if token := self.request.cookies.get(COOKIE_NAME):
            claims = decode_session(token)
            principal = DashboardPrincipal.from_session(claims)
            if self.request.url.path.startswith("/dashboard/api/"):
                principal.authorize_path(
                    self.request.url.path, self.request.scope.get("method", "GET")
                )
            async with LangSmithLogin().authorized_person(principal) as (session, user):
                yield session, AuthorizedClaims(claims, user)
            return
        claims = await self.langsmith_claims()
        principal = DashboardPrincipal.from_session(claims)
        if self.request.url.path.startswith("/dashboard/api/"):
            principal.authorize_path(self.request.url.path, self.request.scope.get("method", "GET"))
        async with postgres.session() as session:
            yield session, claims
