"""Admin opt-in rejects channels Slack would not deliver to the webhook."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.slack import dashboard_routes
from agent.slack.channels import SlackChannel
from agent.slack.kitchen_channels import SetKitchenChannel


@pytest.mark.parametrize(
    ("payload", "allowed"),
    [
        ({"is_member": True, "is_ext_shared": False, "is_pending_ext_shared": False}, True),
        ({"is_member": True, "is_ext_shared": False, "is_pending_ext_shared": True}, False),
        ({"is_member": True, "is_ext_shared": True, "is_pending_ext_shared": False}, False),
        ({"is_member": False, "is_ext_shared": False, "is_pending_ext_shared": False}, False),
    ],
)
async def test_channel_opt_in_checks_fresh_slack_eligibility(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, bool], allowed: bool
) -> None:
    load = AsyncMock(return_value=SlackChannel(id="C123", payload=payload))
    put = AsyncMock()
    monkeypatch.setattr(dashboard_routes.SlackChannel, "load", load)
    monkeypatch.setattr(dashboard_routes.KITCHEN_CHANNELS, "put", put)

    if allowed:
        await dashboard_routes.api_enable_kitchen_channel(
            SetKitchenChannel(channel_id="C123"), {"sub": "admin"}
        )
        put.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as error:
            await dashboard_routes.api_enable_kitchen_channel(
                SetKitchenChannel(channel_id="C123"), {"sub": "admin"}
            )
        assert error.value.status_code == 400
        put.assert_not_awaited()
    load.assert_awaited_once_with("C123", use_cache=False)
