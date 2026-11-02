from unittest.mock import AsyncMock, patch

import pytest

from openswe.tools.add_review_comment import add_review_comment


@pytest.mark.asyncio
@pytest.mark.parametrize("review_page", [None, "https://github.com/acme/app/pull/8"])
async def test_acts_only_on_a_message_from_the_pull_requests_review_page(
    review_page: str | None,
) -> None:
    config = {
        "configurable": {
            "github_login": "alice",
            "repo": {"owner": "acme", "name": "app"},
            "pr_number": 7,
            **({"review_page_pr_url": review_page} if review_page else {}),
        }
    }
    as_user = AsyncMock()
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch("openswe.tools.add_review_comment.pr_author_login", AsyncMock(return_value="alice")),
        patch("openswe.tools.add_review_comment.GitHubClient.as_user", as_user),
    ):
        result = await add_review_comment(file="a.py", line=3, body="Nit")

    assert result["added"] is False
    assert "review page" in str(result["error"])
    as_user.assert_not_called()
