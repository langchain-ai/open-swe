"""Move the legacy ``user_mappings`` Store records into ``users``.

Before the ``users`` table, a person was a Store record keyed by GitHub login:
``github_login`` ⇄ ``work_email`` ⇄ ``slack_user_id``. This copies each one
into a ``users`` row with a GitHub identity (and a Slack one when the record
has a member id), then deletes the record, so the import is idempotent and a
later release can drop this module. It is the only reader of that namespace.

GitHub identities are keyed by the account's numeric id, which the records
never stored, so a login with no ``users`` row costs one ``GET /users/{login}``
with the GitHub App's token. A record that cannot be completed — the lookup
failed, the login is no longer authorized, the record is unreadable — stays
in the Store for the next startup rather than being dropped on the floor.
"""

import logging

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError

from agent.github.app import get_github_app_installation_token
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.store import delete_value, search_all_values
from agent.users.authorization import UnauthorizedUser
from agent.users.models import User

logger = logging.getLogger(__name__)

USER_MAPPINGS_NAMESPACE: list[str] = ["user_mappings"]


class _Mapping(BaseModel):
    model_config = ConfigDict(extra="ignore")

    github_login: str
    work_email: str = ""
    slack_user_id: str | None = None
    status: str = "active"


class _GithubAccount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    login: str


async def import_user_mappings() -> int:
    """Copy every legacy mapping into ``users``; returns how many were imported."""
    records = await search_all_values(USER_MAPPINGS_NAMESPACE)
    if not records:
        return 0
    token = await get_github_app_installation_token()
    if token is None:
        logger.warning(
            "No GitHub App token; legacy mappings for people who never signed in stay put"
        )
    imported = 0
    skipped = 0
    for value in records:
        try:
            mapping = _Mapping.model_validate(value)
        except ValidationError:
            skipped += 1
            logger.error("Skipping an unreadable legacy user mapping", exc_info=True)
            continue
        login = mapping.github_login.strip()
        if not login or mapping.status != "active":
            skipped += 1
            logger.warning(
                "Skipping a legacy user mapping that cannot be imported",
                extra={"github_login": login, "mapping_status": mapping.status},
            )
            continue
        if await _import_mapping(mapping, token):
            imported += 1
            await delete_value(USER_MAPPINGS_NAMESPACE, login.lower())
        else:
            skipped += 1
    log = logger.error if skipped else logger.info
    log(
        "Legacy user mappings processed",
        extra={"imported_users": imported, "skipped_users": skipped},
    )
    return imported


async def _import_mapping(mapping: _Mapping, token: str | None) -> bool:
    login = mapping.github_login.strip()
    email = mapping.work_email.strip().lower()
    user = await User.for_login("github", login)
    if user is None:
        if token is None:
            return False
        account = await _github_account(login, token)
        if account is None:
            return False
        try:
            user = await User.sign_in("github", str(account.id), login=account.login, email=email)
        except UnauthorizedUser:
            logger.warning(
                "Legacy user mapping names a login that is no longer authorized",
                extra={"github_login": login},
            )
            return False
    slack_user_id = (mapping.slack_user_id or "").strip()
    if slack_user_id:
        # The work address belongs on the Slack identity, where it keeps the
        # precedence ``User.email`` gives it.
        await user.link("slack", slack_user_id, email=email)
        return True
    if not email:
        return True
    # No Slack identity to carry the work address, so the GitHub one has to. Let
    # it through only when that would not overwrite a different address: the
    # record is the sole copy, and reporting success deletes it.
    github = next(identity for identity in user.identities if identity.provider == "github")
    stored = github.email.strip().lower()
    if not stored:
        await user.link("github", github.external_id, email=email)
        return True
    if stored == email:
        return True
    logger.error(
        "Keeping a legacy user mapping whose work email would be lost",
        extra={"github_login": login, "stored_email": stored},
    )
    return False


async def _github_account(login: str, token: str) -> _GithubAccount | None:
    url = f"{GITHUB_API_BASE}/users/{login}"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
            return _GithubAccount.model_validate(response.json())
    except httpx2.HTTPError, ValueError, ValidationError:
        logger.warning(
            "Could not look up a GitHub account for a legacy user mapping",
            extra={"github_login": login},
            exc_info=True,
        )
        return None
