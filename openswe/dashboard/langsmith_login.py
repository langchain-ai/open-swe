"""LangSmith login and live organization authorization for independent accounts."""

import logging
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from openswe.config import ENV
from openswe.dashboard.langsmith_oauth import langsmith_issuer, langsmith_oauth_configured
from openswe.dashboard.oauth import hash_state_nonce, issue_state, pkce_s256_challenge
from openswe.dashboard.oauth_credentials import (
    OAuthCredential,
    delete_credential,
    load_credential,
    save_credential,
)
from openswe.dashboard.oauth_refresh import refresh_guard
from openswe.encryption import decrypt_token, encrypt_token
from openswe.users.authorization import UnauthorizedUser
from openswe.utils.http_pool import pooled_transport

if TYPE_CHECKING:
    from openswe.dashboard.principal import DashboardPrincipal

logger = logging.getLogger(__name__)

LANGSMITH_AUTHORIZATION_TTL = timedelta(seconds=60)
LANGSMITH_LOGIN_CALLBACK_PATH = "/dashboard/api/auth/langsmith/callback"


class LangSmithAccount(BaseModel):
    sub: str = Field(min_length=1)
    ls_org_id: str
    email: str = ""
    name: str = ""
    picture: str = ""

    def require_allowed_org(self) -> None:
        if (
            not ENV.LANGSMITH_LOGIN_ORG_ID.get()
            or self.ls_org_id != ENV.LANGSMITH_LOGIN_ORG_ID.get()
        ):
            raise UnauthorizedUser("This LangSmith organization is not authorized")


class LangSmithLogin:
    def __init__(self) -> None:
        self.issuer = langsmith_issuer()
        self.client_id = ENV.LANGSMITH_OAUTH_CLIENT_ID.get()
        self.client_secret = ENV.LANGSMITH_OAUTH_CLIENT_SECRET.get()
        self.org_id = ENV.LANGSMITH_LOGIN_ORG_ID.get()

    def require_configured(self) -> None:
        if not self.org_id or not langsmith_oauth_configured():
            raise HTTPException(503, "LangSmith login is not configured")

    def same_origin(self, url: str) -> str:
        issuer, endpoint = urlparse(self.issuer), urlparse(url)
        if (issuer.scheme, issuer.netloc) != (endpoint.scheme, endpoint.netloc):
            raise HTTPException(502, "LangSmith OAuth endpoint is on another origin")
        if endpoint.username or endpoint.password or endpoint.fragment:
            raise HTTPException(502, "Invalid LangSmith OAuth endpoint")
        return url

    async def request(
        self, method: str, url: str, *, token: str | None = None, data: dict[str, str] | None = None
    ) -> object:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx2.AsyncClient(
                timeout=httpx2.Timeout(15.0, connect=5.0), transport=pooled_transport()
            ) as client:
                response = await client.request(
                    method, self.same_origin(url), headers=headers, data=data
                )
        except httpx2.RequestError as exc:
            raise HTTPException(503, "LangSmith authorization is temporarily unavailable") from exc
        if response.is_success and not response.content:
            return None
        try:
            body: object = response.json()
        except ValueError as exc:
            raise HTTPException(502, "Invalid LangSmith authorization response") from exc
        if not response.is_success:
            if response.status_code in {401, 403} or (
                isinstance(body, dict) and body.get("error") == "invalid_grant"
            ):
                raise HTTPException(
                    401, "LangSmith authorization expired or revoked — sign in again"
                )
            raise HTTPException(503, "LangSmith authorization is temporarily unavailable")
        return body

    async def metadata(self) -> dict[str, str]:
        self.require_configured()
        data = await self.request("GET", self.issuer + "/.well-known/openid-configuration")
        if not isinstance(data, dict) or data.get("issuer") != self.issuer:
            raise HTTPException(502, "Invalid LangSmith OAuth issuer")
        endpoints: dict[str, str] = {}
        for name in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
            endpoint = data.get(name)
            if not isinstance(endpoint, str) or not endpoint:
                raise HTTPException(502, "Incomplete LangSmith OAuth discovery")
            endpoints[name] = self.same_origin(endpoint)
        revocation_endpoint = data.get("revocation_endpoint")
        if isinstance(revocation_endpoint, str) and revocation_endpoint:
            endpoints["revocation_endpoint"] = self.same_origin(revocation_endpoint)
        return endpoints

    async def start(
        self,
        *,
        redirect_uri: str,
        redirect_to: str,
        nonce: str,
        handoff_challenge: str | None,
        handoff_port: int | None,
    ) -> str:
        endpoints = await self.metadata()
        verifier = secrets.token_urlsafe(32)
        state = issue_state(
            redirect_to=redirect_to,
            nonce_hash=hash_state_nonce(nonce),
            handoff_challenge=handoff_challenge,
            handoff_port=handoff_port,
            provider="langsmith",
            encrypted_code_verifier=encrypt_token(verifier),
            redirect_uri=redirect_uri,
        )
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": pkce_s256_challenge(verifier),
            "code_challenge_method": "S256",
            "scope": "openid profile email offline_access",
            "resource": self.issuer,
        }
        return endpoints["authorization_endpoint"] + "?" + urlencode(params)

    async def account(
        self, token: str, *, userinfo_endpoint: str | None = None
    ) -> LangSmithAccount:
        self.require_configured()
        data = await self.request(
            "GET", userinfo_endpoint or self.issuer + "/userinfo", token=token
        )
        try:
            account = LangSmithAccount.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(502, "Invalid LangSmith identity response") from exc
        try:
            account.require_allowed_org()
        except UnauthorizedUser as exc:
            raise HTTPException(403, "Your LangSmith organization is not authorized") from exc
        organizations = await self.request(
            "GET", self.issuer + "/orgs?skip_create=true", token=token
        )
        if not isinstance(organizations, list):
            raise HTTPException(502, "Invalid LangSmith organization response")
        if not any(isinstance(org, dict) and org.get("id") == self.org_id for org in organizations):
            raise HTTPException(
                403, "Your LangSmith account is not a member of the authorized organization"
            )
        return account

    async def complete(
        self, state: Mapping[str, object], code: str
    ) -> tuple[LangSmithAccount, dict[str, object], str]:
        if state.get("provider") != "langsmith":
            raise HTTPException(400, "Invalid LangSmith OAuth state")
        encrypted_verifier = state.get("encrypted_code_verifier")
        redirect_uri = state.get("redirect_uri")
        if not isinstance(encrypted_verifier, str) or not isinstance(redirect_uri, str):
            raise HTTPException(400, "Incomplete LangSmith OAuth state")
        verifier = decrypt_token(encrypted_verifier)
        if not verifier:
            raise HTTPException(400, "Invalid LangSmith OAuth verifier")
        endpoints = await self.metadata()
        data = await self.request(
            "POST",
            endpoints["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": self.issuer,
            },
        )
        tokens = self.tokens(data)
        account = await self.account(
            str(tokens["access_token"]), userinfo_endpoint=endpoints["userinfo_endpoint"]
        )
        return account, tokens, endpoints["token_endpoint"]

    @staticmethod
    def tokens(data: object) -> dict[str, object]:
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("access_token"), str)
            or not data["access_token"]
        ):
            raise HTTPException(502, "LangSmith OAuth returned no access token")
        return data

    async def save(
        self,
        key: str,
        tokens: dict[str, object],
        token_endpoint: str,
        *,
        previous: OAuthCredential | None = None,
        email: str | None = None,
        authorized_until: datetime | None = None,
    ) -> None:
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in")
        if not isinstance(expires_in, int | float) or expires_in <= 0:
            raise HTTPException(502, "LangSmith OAuth returned no token expiry")
        await save_credential(
            "langsmith_login",
            key,
            encrypted_access_token=encrypt_token(str(tokens["access_token"])),
            encrypted_refresh_token=encrypt_token(refresh_token)
            if isinstance(refresh_token, str) and refresh_token
            else previous.encrypted_refresh_token
            if previous
            else None,
            access_token_expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            client_id=self.client_id,
            token_endpoint=self.same_origin(token_endpoint),
            account_email=email,
            login_authorized_until=authorized_until,
            login_authorized_org_id=self.org_id if authorized_until else None,
        )

    async def access_token(self, key: str) -> str | None:
        from openswe.dashboard.principal import DashboardPrincipal
        from openswe.users import User

        user_id = User.key_id(key)
        if user_id is None:
            return None
        user = await User.get(user_id)
        identity = (
            next((item for item in user.identities if item.provider == "langsmith"), None)
            if user
            else None
        )
        if identity is None:
            return None
        principal = DashboardPrincipal("langsmith", key, user_id, identity.external_id)
        return await self.authorize_session(principal)

    async def credential(self, key: str) -> OAuthCredential:
        credential = await load_credential("langsmith_login", key)
        if credential is None or credential.client_id != self.client_id:
            raise HTTPException(401, "LangSmith login expired — sign in again")
        return credential

    def needs_refresh(self, credential: OAuthCredential) -> bool:
        return (
            credential.access_token_expires_at is None
            or datetime.now(UTC) + timedelta(minutes=2) >= credential.access_token_expires_at
        )

    def checked_token(self, credential: OAuthCredential) -> str:
        token = decrypt_token(credential.encrypted_access_token)
        if not token:
            raise HTTPException(401, "LangSmith login expired — sign in again")
        return token

    async def check_identity(self, token: str, principal: DashboardPrincipal) -> None:
        account = await self.account(token)
        if account.sub != principal.langsmith_subject:
            raise HTTPException(401, "LangSmith login identity changed — sign in again")

    async def refresh(self, principal: DashboardPrincipal) -> str | None:
        async with refresh_guard("langsmith_login", principal.subject):
            credential = await self.credential(principal.subject)
            if not self.needs_refresh(credential):
                return None
            refresh = decrypt_token(credential.encrypted_refresh_token or "")
            if not refresh:
                raise HTTPException(401, "LangSmith login expired — sign in again")
            data = await self.request(
                "POST",
                credential.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": credential.client_id,
                    "client_secret": self.client_secret,
                    "resource": self.issuer,
                },
            )
            tokens = self.tokens(data)
            token = str(tokens["access_token"])
            authorized_until = datetime.now(UTC) + LANGSMITH_AUTHORIZATION_TTL
            await self.check_identity(token, principal)
            await self.save(
                principal.subject,
                tokens,
                credential.token_endpoint,
                previous=credential,
                email=credential.account_email,
                authorized_until=authorized_until,
            )
            return token

    async def authorize_credential(
        self, credential: OAuthCredential, principal: DashboardPrincipal
    ) -> str:
        token = self.checked_token(credential)
        now = datetime.now(UTC)
        if (
            credential.login_authorized_org_id == self.org_id
            and credential.login_authorized_until is not None
            and credential.login_authorized_until > now
            and credential.access_token_expires_at is not None
            and credential.access_token_expires_at > now
        ):
            return token
        authorized_until = now + LANGSMITH_AUTHORIZATION_TTL
        await self.check_identity(token, principal)
        if not await credential.authorize_login(self.org_id, authorized_until):
            raise HTTPException(401, "LangSmith login expired — sign in again")
        return token

    async def authorize_session(self, principal: DashboardPrincipal) -> str:
        self.require_configured()
        await principal.user()
        key = principal.subject
        credential = await self.credential(key)
        try:
            if self.needs_refresh(credential):
                token = await self.refresh(principal)
                if token is not None:
                    return token
                credential = await self.credential(key)
            return await self.authorize_credential(credential, principal)
        except HTTPException as exc:
            if exc.status_code in {401, 403}:
                await delete_credential(
                    "langsmith_login",
                    key,
                    encrypted_access_token=credential.encrypted_access_token,
                )
            raise

    async def logout(self, key: str) -> None:
        async with refresh_guard("langsmith_login", key):
            credential = await load_credential("langsmith_login", key)
            await delete_credential("langsmith_login", key)
        if credential is None:
            return
        try:
            endpoints = await self.metadata()
        except HTTPException:
            logger.warning("Could not discover LangSmith revocation endpoint", exc_info=True)
            return
        endpoint = endpoints.get("revocation_endpoint")
        if endpoint is None:
            return
        for encrypted in (
            credential.encrypted_refresh_token,
            credential.encrypted_access_token,
        ):
            token = decrypt_token(encrypted or "")
            if token:
                try:
                    await self.request(
                        "POST",
                        endpoint,
                        data={
                            "token": token,
                            "client_id": credential.client_id,
                            "client_secret": self.client_secret,
                        },
                    )
                except HTTPException:
                    logger.warning("Could not revoke LangSmith token remotely", exc_info=True)
