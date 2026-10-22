"""An isolated controller App whose credentials never enter agent credential caches."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from githubkit.auth import AppAuthStrategy, TokenAuthStrategy
from pydantic import AliasPath, BaseModel, ConfigDict, Field, SecretStr

from openswe.config import ENV
from openswe.github.http import scoped_installation_token
from openswe.github.sdk import GITHUB_API_VERSION, github_sdk

QUEUE_PERMISSIONS = {
    "actions": "write",
    "checks": "read",
    "contents": "write",
    "metadata": "read",
    "pull_requests": "read",
    "statuses": "read",
    "variables": "read",
}


class AppIdentity(BaseModel):
    id: int
    slug: str
    owner: str = Field(validation_alias=AliasPath("owner", "login"))


class InstallationIdentity(BaseModel):
    app_id: int
    owner: str = Field(validation_alias=AliasPath("account", "login"))
    repository_selection: str
    suspended_at: datetime | None


class InstalledRepository(BaseModel):
    id: int
    full_name: str


class InstallationToken(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    token: SecretStr
    expires_at: datetime
    permissions: dict[str, str]
    repositories: tuple[InstalledRepository, ...]


@dataclass(frozen=True)
class QueueApp:
    app_id: int
    installation_id: int
    repository_id: int
    repository: str
    actor_id: int
    private_key: SecretStr = field(repr=False)

    async def token(self) -> str:
        if self.app_id == ENV.GITHUB_APP_ID.get_int(0):
            raise ValueError("Agent and queue Apps must be separate")
        auth = AppAuthStrategy(
            self.app_id, self.private_key.get_secret_value().replace("\\n", "\n")
        )
        async with github_sdk(auth) as client:
            api = client.rest(GITHUB_API_VERSION)
            app = AppIdentity.model_validate((await api.apps.async_get_authenticated()).json())
            installation = InstallationIdentity.model_validate(
                (await api.apps.async_get_installation(self.installation_id)).json()
            )
            if (
                app.id != self.app_id
                or installation.app_id != self.app_id
                or app.owner.lower() != installation.owner.lower()
                or installation.owner.lower() != self.repository.split("/")[0].lower()
                or installation.repository_selection != "selected"
                or installation.suspended_at is not None
            ):
                raise ValueError("Queue App installation does not match its configured owner")
            response = await scoped_installation_token(
                client, self.installation_id, self.repository_id, QUEUE_PERMISSIONS
            )
        granted = InstallationToken.model_validate(response)
        if (
            [(repo.id, repo.full_name.lower()) for repo in granted.repositories]
            != [(self.repository_id, self.repository.lower())]
            or granted.permissions != QUEUE_PERMISSIONS
            or granted.expires_at <= datetime.now(UTC) + timedelta(minutes=10)
        ):
            raise ValueError("Queue token has unexpected scope or expiry")
        async with github_sdk(TokenAuthStrategy(granted.token.get_secret_value())) as client:
            actor = (
                await client.rest(GITHUB_API_VERSION).users.async_get_by_username(
                    f"{app.slug}[bot]"
                )
            ).json()
        if actor.get("id") != self.actor_id or actor.get("type") != "Bot":
            raise ValueError("Queue dispatch actor does not belong to the controller App")
        return granted.token.get_secret_value()
