import pytest

from openswe.dashboard.profiles import ProfileUpdate, get_profile, upsert_profile


@pytest.mark.parametrize("flag", ["auto_human_review_enabled", "github_review_request_comments"])
async def test_review_preferences_are_personal_and_survive_other_profile_edits(user_records, flag):
    await upsert_profile("alice", "", ProfileUpdate.model_validate({flag: True}))
    await upsert_profile("bob", "", ProfileUpdate.model_validate({flag: False}))
    await upsert_profile("alice", "", ProfileUpdate(draft_prs=False))
    alice = await get_profile("alice")
    bob = await get_profile("bob")
    assert alice is not None and alice[flag] is True
    assert bob is not None and bob[flag] is False
    await upsert_profile("alice", "", ProfileUpdate.model_validate({flag: False}))
    alice = await get_profile("alice")
    assert alice is not None and alice[flag] is False
