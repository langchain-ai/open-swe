from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from openswe.sandboxes.environment import EnvironmentUpdate, thread_environment


@pytest.mark.parametrize("visibility", ["public", None, "private"])
async def test_personal_values_only_reach_private_sandboxes(visibility: str | None) -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {"workspace": "team", "owner_login": "alice", "visibility": visibility}
    }
    with (
        patch("langgraph_sdk.get_client", return_value=client),
        patch(
            "openswe.sandboxes.environment._load_environment",
            AsyncMock(side_effect=[{"KEY": "team", "OTHER": "shared"}, {"KEY": "personal"}]),
        ) as load,
    ):
        result = await thread_environment("thread")
    assert result == {"KEY": "personal" if visibility == "private" else "team", "OTHER": "shared"}
    assert load.await_count == (2 if visibility == "private" else 1)
    assert client.threads.update.await_count == (1 if visibility == "private" else 0)


def test_reserved_variables_cannot_replace_tool_authentication() -> None:
    with pytest.raises(ValidationError):
        EnvironmentUpdate(variables={"OPEN_SWE_TOOLS_URL": "https://evil.example"})
