from unittest.mock import MagicMock

from openswe.middleware.model_call_shape import CallShape
from openswe.review_guide.middleware import ReviewGuideMiddleware


async def test_a_resumed_run_keeps_the_walkthrough_rules_from_its_checkpoint() -> None:
    """A run resumed past its before-agent step builds a fresh middleware; the rules are in state."""
    shaped = await ReviewGuideMiddleware(thread_id="t", approve_ts="").ashape_model_call(
        CallShape(system_prompt="base prompt"),
        {"messages": [], "review_guide_rules": "walkthrough rules"},
        MagicMock(),
    )

    assert shaped.system_prompt == "base prompt\n\nwalkthrough rules"
