from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from openswe.review.enabled_repos import list_enabled_review_repos, set_review_repo_enabled
from openswe.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from openswe.tools.manage_review_repos import manage_review_repos


@pytest.fixture(autouse=True)
def requester(grant_tool_access: Callable[..., None]):
    grant_tool_access(admin=True, admin_surface=True)
    with (
        patch(
            "openswe.run_config.get_config",
            return_value={"configurable": {"github_login": "admin"}},
        ),
        patch(
            "openswe.tools.manage_review_repos.require_repo_access_for_user",
            AsyncMock(return_value="t"),
        ),
    ):
        yield


async def test_toggle_keeps_other_repositories_and_settings(registry_db: None) -> None:
    await set_review_repo_enabled("o/other", True)
    await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="off"))
    assert await manage_review_repos("read", "O/R") == {"repository": "o/r", "enabled": False}
    assert await manage_review_repos("set", "O/R", True) == {"repository": "o/r", "enabled": True}
    assert await list_enabled_review_repos() == ["o/other", "o/r"]
    await manage_review_repos("set", "o/r", False)
    assert await list_enabled_review_repos() == ["o/other"]
    style = await REVIEW_STYLES.get("o/r")
    assert style is not None and style.approval_mode == "off"


@pytest.mark.parametrize("admin,surface,sole", [(False, True, False), (True, False, False)])
async def test_unauthorized_calls_cannot_read_or_write(
    registry_db: None,
    grant_tool_access: Callable[..., None],
    admin: bool,
    surface: bool,
    sole: bool,
) -> None:
    await set_review_repo_enabled("private/r", True)
    grant_tool_access(admin=admin, admin_surface=surface, sole=sole)
    for action in ("read", "set"):
        result = await manage_review_repos(action, "private/r", False if action == "set" else None)
        assert "not available in this thread" in str(result["error"])
        assert "enabled" not in result
    assert await list_enabled_review_repos() == ["private/r"]


async def test_repository_access_denial_prevents_mutation(registry_db: None) -> None:
    with (
        patch(
            "openswe.tools.manage_review_repos.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "No access")),
        ),
        pytest.raises(HTTPException),
    ):
        await manage_review_repos("set", "private/r", True)
    assert await list_enabled_review_repos() == []


async def test_sole_writer_can_set_but_not_read(
    registry_db: None, grant_tool_access: Callable[..., None]
) -> None:
    await set_review_repo_enabled("private/other", True)
    grant_tool_access(admin=True, sole=True)
    assert await manage_review_repos("set", "o/r", True) == {
        "ok": True,
        "repository": "o/r",
        "enabled": True,
    }
    assert "not available in this thread" in str(
        (await manage_review_repos("read", "o/r"))["error"]
    )
    assert await list_enabled_review_repos() == ["o/r", "private/other"]


@pytest.mark.parametrize("action,enabled", [("set", None), ("set", "yes"), ("read", False)])
async def test_invalid_arguments_do_not_write(
    registry_db: None, action: str, enabled: object
) -> None:
    with pytest.raises(ValueError):
        await manage_review_repos(action, "o/r", enabled)
    assert await list_enabled_review_repos() == []
