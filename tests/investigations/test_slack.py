import pytest

from agent.investigations import slack
from agent.investigations.models import InvestigationPolicy


@pytest.mark.parametrize(
    "change",
    [
        {"is_private": True},
        {"is_ext_shared": True},
        {"is_pending_ext_shared": True},
        {"is_mpim": True},
        {"is_im": True},
        {"is_channel": False},
    ],
)
def test_public_internal_gate_cannot_be_bypassed_by_matching_name(change):
    channel = {
        "id": "C1",
        "name": "inc-api",
        "is_channel": True,
        "is_private": False,
        "is_im": False,
        "is_mpim": False,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    }
    assert slack.channel_allowed(channel, InvestigationPolicy())
    assert not slack.channel_allowed(channel | change, InvestigationPolicy())


def test_channel_names_and_explicit_exclusions_are_enforced():
    channel = {
        "id": "C1",
        "name": "inc-api",
        "is_channel": True,
        "is_private": False,
        "is_im": False,
        "is_mpim": False,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    }
    assert not slack.channel_allowed(channel, InvestigationPolicy(excluded_channel_ids=["C1"]))
    assert not slack.channel_allowed(channel | {"name": "general"}, InvestigationPolicy())
