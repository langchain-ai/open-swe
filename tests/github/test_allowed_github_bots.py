import pytest
from fastapi import HTTPException

from agent.github import allowed_bots
from agent.github.allowed_bots import AllowedGitHubBot, AllowGitHubBot

pytestmark = pytest.mark.usefixtures("registry_db")


def _github_account(monkeypatch: pytest.MonkeyPatch, **account: object) -> None:
    async def fetch(login: str) -> allowed_bots._GitHubUser:
        return allowed_bots._GitHubUser.model_validate(account)

    monkeypatch.setattr(allowed_bots, "_fetch_github_account", fetch)


async def test_allowing_a_bot_stores_its_canonical_login(monkeypatch) -> None:
    _github_account(monkeypatch, id=29139614, login="Vercel[bot]", type="Bot")

    bot = await AllowedGitHubBot.allow(AllowGitHubBot(login="@vercel[bot]"), created_by="ada")

    assert (bot.github_id, bot.login, bot.created_by) == (29139614, "Vercel[bot]", "ada")
    assert await AllowedGitHubBot.logins() == frozenset({"vercel[bot]"})
    with pytest.raises(HTTPException) as duplicate:
        await AllowedGitHubBot.allow(AllowGitHubBot(login="vercel[bot]"), created_by="ada")
    assert duplicate.value.status_code == 409

    await AllowedGitHubBot.remove(29139614)
    assert await AllowedGitHubBot.logins() == frozenset()


@pytest.mark.parametrize(
    ("login", "account_type"),
    [("octocat", "User"), ("open-swe[bot]", "Bot")],
)
async def test_allowing_rejects_humans_and_open_swe_itself(
    monkeypatch, login: str, account_type: str
) -> None:
    _github_account(monkeypatch, id=1, login=login, type=account_type)

    with pytest.raises(HTTPException) as rejected:
        await AllowedGitHubBot.allow(AllowGitHubBot(login=login), created_by="ada")

    assert rejected.value.status_code == 400
    assert await AllowedGitHubBot.list_all() == []
