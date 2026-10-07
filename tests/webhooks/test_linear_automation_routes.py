import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from openswe.linear import routes


def _request(payload: dict[str, object]) -> AsyncMock:
    request = AsyncMock()
    request.body.return_value = json.dumps(payload).encode()
    request.headers = {"Linear-Signature": "valid", "Linear-Delivery": "delivery-1"}
    return request


async def test_issue_deliveries_check_automations() -> None:
    payload = {"type": "Issue", "action": "create", "webhookTimestamp": int(time.time() * 1000)}
    background_tasks = MagicMock()
    with patch("openswe.webhooks.common.verify_linear_signature", return_value=True):
        response = await routes.linear_webhook(_request(payload), background_tasks)

    assert response["status"] == "accepted"
    background_tasks.add_task.assert_called_once_with(
        routes._launch_automations, payload, "delivery-1"
    )


async def test_a_stale_delivery_is_rejected_as_a_replay() -> None:
    payload = {"type": "Issue", "action": "create", "webhookTimestamp": 1_000}
    background_tasks = MagicMock()
    with (
        patch("openswe.webhooks.common.verify_linear_signature", return_value=True),
        pytest.raises(HTTPException) as rejected,
    ):
        await routes.linear_webhook(_request(payload), background_tasks)

    assert rejected.value.status_code == 401
    background_tasks.add_task.assert_not_called()
