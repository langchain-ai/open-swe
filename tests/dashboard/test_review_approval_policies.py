from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from agent.dashboard import review_approval_policies as policies
from agent.dashboard.team_settings import TeamSettingsUpdate


@pytest.mark.asyncio
async def test_review_approval_policy_defaults_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.store.get_item = AsyncMock(return_value=None)
    monkeypatch.setattr(policies, "_client", lambda: client)

    policy = await policies.get_review_approval_policy("Owner/Repo")

    assert policy == {
        "full_name": "owner/repo",
        "enabled": False,
        "threshold": None,
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_set_review_approval_policy_normalizes_and_preserves_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.store.get_item = AsyncMock(
        return_value={
            "value": {
                "policies": {
                    "other/repo": {
                        "full_name": "other/repo",
                        "enabled": True,
                        "threshold": 80,
                    }
                }
            }
        }
    )
    client.store.put_item = AsyncMock()
    monkeypatch.setattr(policies, "_client", lambda: client)

    result = await policies.set_review_approval_policy("Owner/Repo", enabled=True, threshold=95)

    assert result["full_name"] == "owner/repo"
    assert result["enabled"] is True
    assert result["threshold"] == 95
    assert client.store.put_item.await_args is not None
    stored = client.store.put_item.await_args.args[2]["policies"]
    assert stored["other/repo"]["threshold"] == 80
    assert stored["owner/repo"]["threshold"] == 95


@pytest.mark.parametrize("value", [-1, 101, True])
def test_team_auto_approve_threshold_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(auto_approve_default_threshold=value)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_effective_policy_inherits_and_overrides_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        policies,
        "get_team_settings",
        AsyncMock(
            return_value={
                "auto_approve_enabled": True,
                "auto_approve_default_threshold": 92,
            }
        ),
    )
    monkeypatch.setattr(
        policies,
        "get_review_approval_policy",
        AsyncMock(
            return_value={
                "full_name": "owner/repo",
                "enabled": True,
                "threshold": 80,
                "updated_at": None,
            }
        ),
    )

    effective = await policies.get_effective_review_approval_policy("owner", "repo")

    assert effective["effective_enabled"] is True
    assert effective["effective_threshold"] == 80
    assert effective["team_threshold"] == 92


def test_team_auto_approve_defaults_disabled() -> None:
    settings = TeamSettingsUpdate()
    assert settings.auto_approve_enabled is False
    assert settings.auto_approve_default_threshold == 90
