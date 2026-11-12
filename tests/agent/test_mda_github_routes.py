"""``mda/rt/github_routes.py`` routes GitHub deliveries with the backend's action sets and handles.

MDA's GitHub handler keeps its own copies because ``mda/`` never imports the backend
(AGENTS.md); the backend checks the sender, the gate and the work again, so a drift
would start the wrong work or none rather than let anyone in, but it must not drift.
"""

import pytest

from openswe.github.comments import OPEN_SWE_TAGS, ROBIN_NOTIFICATION_MARKER
from openswe.webhooks import common
from tests.github_channel import MDA_ROUTES


def test_the_action_sets_are_the_backends() -> None:
    assert MDA_ROUTES.ISSUE_ACTIONS == common.SUPPORTED_GH_ISSUE_ACTIONS
    assert MDA_ROUTES.COMMENT_ACTIONS == common.SUPPORTED_GH_COMMENT_ACTIONS
    assert MDA_ROUTES.NOTIFICATION_MARKER == ROBIN_NOTIFICATION_MARKER


@pytest.mark.parametrize(
    "text",
    [
        "@openswe please fix",
        "@OpenSWE please fix",
        "please @open-swe.",
        "@openswe-dev take a look",
        "@openswe-preview take a look",
        "@opensweet",
        "email me at x@openswe.dev",
        "@robin-agent please fix",
        "ping@openswe now",
        "see docs.@openswe",
        "cc @robin-agent-dev",
        "`@openswe` in code",
        "no handle",
        "",
    ],
)
def test_mentions_are_the_backends(text: str) -> None:
    assert MDA_ROUTES.mentions(text, OPEN_SWE_TAGS) is common.mentions_open_swe(text)
