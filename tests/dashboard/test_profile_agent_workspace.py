import pytest

from openswe.dashboard.profiles import (
    ProfileUpdate,
    get_profile,
    normalize_profile_for_response,
    upsert_profile,
)


@pytest.mark.parametrize("initial", [{}, {"experimental_agent_workspace": None}])
async def test_desktop_experience_defaults_on_and_opt_out_survives_edits(user_records, initial):
    user_records.seed("profile", "alice", initial)
    assert normalize_profile_for_response(initial)["experimental_agent_workspace"] is True
    await upsert_profile("alice", "", ProfileUpdate(experimental_agent_workspace=False))
    await upsert_profile("alice", "", ProfileUpdate(draft_prs=False))
    alice = await get_profile("alice")
    assert alice is not None
    assert normalize_profile_for_response(alice)["experimental_agent_workspace"] is False
    assert (
        normalize_profile_for_response(await get_profile("bob") or {})[
            "experimental_agent_workspace"
        ]
        is True
    )
    await upsert_profile("alice", "", ProfileUpdate(experimental_agent_workspace=None))
    assert (
        normalize_profile_for_response(await get_profile("alice") or {})[
            "experimental_agent_workspace"
        ]
        is True
    )
