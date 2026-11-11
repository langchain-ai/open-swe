"""Provider boundaries for exercising the real dashboard OAuth callbacks."""

import secrets
from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openswe.dashboard.langsmith_login import LangSmithLogin
from openswe.dashboard.oauth import GithubUser, pkce_s256_challenge
from openswe.users import User


class OAuthProviders:
    def __init__(self, base_url: str) -> None:
        self.issuer = base_url + "/fake-gh/langsmith"
        self.codes: dict[str, tuple[str, str, str]] = {}
        self.tokens: dict[str, str] = {}

    async def provision(self, user: User, email: str) -> User:
        token = secrets.token_urlsafe()
        self.tokens[token] = email
        login = LangSmithLogin()
        account = await login.account(token)
        signed_in = await User.sign_in("langsmith", account.sub, email=email, authorization=account)
        github = next(identity for identity in user.identities if identity.provider == "github")
        user = await signed_in.join_github(github.external_id, login=user.github_login, email=email)
        await login.save(
            f"user:{user.id}",
            {"access_token": token, "expires_in": 3600},
            self.issuer + "/token",
            email=email,
        )
        return user

    def install(self, app: FastAPI) -> None:
        app.add_api_route(self.issuer_path + "/.well-known/openid-configuration", self.metadata)
        app.add_api_route(self.issuer_path + "/authorize", self.authorize)
        app.add_api_route(self.issuer_path + "/token", self.token, methods=["POST"])
        app.add_api_route(self.issuer_path + "/userinfo", self.userinfo)
        app.add_api_route(self.issuer_path + "/orgs", self.organizations)
        app.add_api_route(self.issuer_path + "/auth/public", self.public_auth)
        app.add_api_route(self.issuer_path + "/revoke", self.revoke, methods=["POST"])
        app.add_api_route("/fake-gh/link/authorize", self.github_authorize)

    @property
    def issuer_path(self) -> str:
        return "/fake-gh/langsmith"

    async def metadata(self) -> dict[str, str]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/authorize",
            "token_endpoint": self.issuer + "/token",
            "userinfo_endpoint": self.issuer + "/userinfo",
            "revocation_endpoint": self.issuer + "/revoke",
        }

    async def authorize(
        self, state: str, redirect_uri: str, code_challenge: str, email: str = ""
    ) -> Response:
        if not email:
            fields = {
                "state": state,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
            }
            hidden = "".join(
                f'<input type="hidden" name="{key}" value="{escape(value, quote=True)}">'
                for key, value in fields.items()
            )
            return HTMLResponse(
                "<h1>LangSmith sign in</h1><form>"
                + hidden
                + '<label>Email <input name="email" type="email" required></label>'
                + "<button>Continue</button></form>"
            )
        code = secrets.token_urlsafe()
        self.codes[code] = (email, redirect_uri, code_challenge)
        return RedirectResponse(redirect_uri + "?" + urlencode({"state": state, "code": code}), 302)

    async def token(self, request: Request) -> dict[str, object]:
        form = await request.form()
        claim = self.codes.pop(str(form.get("code")), None)
        if claim is None:
            raise HTTPException(400, "Unknown or reused authorization code")
        email, redirect_uri, challenge = claim
        if (
            form.get("redirect_uri") != redirect_uri
            or pkce_s256_challenge(str(form.get("code_verifier"))) != challenge
        ):
            raise HTTPException(400, "OAuth redirect or PKCE mismatch")
        token = secrets.token_urlsafe()
        self.tokens[token] = email
        return {"access_token": token, "token_type": "Bearer", "expires_in": 3600}

    async def userinfo(self, request: Request) -> dict[str, str]:
        email = self.tokens.get(request.headers.get("authorization", "").removeprefix("Bearer "))
        if email is None:
            raise HTTPException(401)
        return {"sub": "e2e-langsmith:" + email.lower(), "email": email, "ls_org_id": "e2e-org"}

    async def public_auth(self, request: Request) -> dict[str, str]:
        if request.headers.get("x-api-key") != "e2e-deployment-key":
            raise HTTPException(401)
        return {"organization_id": "e2e-org"}

    async def organizations(self, request: Request) -> list[dict[str, str]]:
        await self.userinfo(request)
        return [{"id": "e2e-org"}]

    async def revoke(self, request: Request) -> dict[str, bool]:
        form = await request.form()
        self.tokens.pop(str(form.get("token")), None)
        return {"ok": True}

    async def github_authorize(self, state: str, redirect_uri: str, login: str = "") -> Response:
        if not login:
            return HTMLResponse(
                "<h1>GitHub account verification</h1><form>"
                f'<input type="hidden" name="state" value="{escape(state, quote=True)}">'
                f'<input type="hidden" name="redirect_uri" value="{escape(redirect_uri, quote=True)}">'
                '<label>GitHub user <input name="login" required></label>'
                "<button>Authorize GitHub</button></form>"
            )
        code = secrets.token_urlsafe()
        self.codes[code] = (login, redirect_uri, "github")
        return RedirectResponse(redirect_uri + "?" + urlencode({"state": state, "code": code}), 302)

    async def exchange_github(self, code: str) -> dict[str, object]:
        claim = self.codes.pop(code, None)
        if claim is None or claim[2] != "github":
            raise HTTPException(400, "Invalid GitHub authorization code")
        token = secrets.token_urlsafe()
        self.tokens[token] = claim[0]
        return {"access_token": token, "token_type": "bearer"}

    async def github_user(self, access_token: str) -> tuple[GithubUser, str]:
        login = self.tokens.get(access_token)
        user = await User.for_login("github", login) if login else None
        if user is None:
            raise HTTPException(401, "Unknown fake GitHub account")
        identity = next(item for item in user.identities if item.provider == "github")
        return GithubUser(
            id=int(identity.external_id), login=user.github_login, email=user.email
        ), user.email
