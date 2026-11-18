import pytest

from openswe.dashboard.profiles import PROFILES, ProfileUpdate, upsert_profile
from openswe.users import User
from openswe.users.records import UserRecords

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_omitted_draft_preference_preserves_existing_value(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "octocat")
    await User.sign_in("github", "1", login="octocat")
    await UserRecords("profile").put(
        "octocat",
        {
            "draft_prs": False,
            "auto_human_review_enabled": False,
            "model_routing_enabled": False,
            "recent_thread_context_enabled": True,
            "experimental_local_auto_mode": True,
            "slack_onboarding_dismissed": True,
        },
    )
    profile = await upsert_profile("octocat", "octocat@example.com", ProfileUpdate())
    assert profile["draft_prs"] is False
    assert profile["auto_human_review_enabled"] is False
    assert "model_routing_enabled" not in profile
    assert "recent_thread_context_enabled" not in profile
    assert "experimental_local_auto_mode" not in profile
    assert profile["slack_onboarding_dismissed"] is True
    assert await PROFILES.get("octocat") == profile


async def test_explicit_draft_preference_is_persisted(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "octocat")
    await User.sign_in("github", "1", login="octocat")
    profile = await upsert_profile("octocat", "octocat@example.com", ProfileUpdate(draft_prs=True))
    assert profile["draft_prs"] is True
    assert await PROFILES.get("octocat") == profile
