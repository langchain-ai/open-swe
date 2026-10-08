from unittest.mock import AsyncMock, MagicMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from openswe.review_guide.middleware import ReviewGuideMiddleware


async def test_a_resumed_run_keeps_the_walkthrough_rules_from_its_checkpoint() -> None:
    """A run resumed past its before-agent step builds a fresh middleware; the rules are in state."""
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="what does this do?")],
        system_message=SystemMessage(content="base prompt"),
        state={"messages": [], "review_guide_rules": "walkthrough rules"},
        runtime=MagicMock(),
    )
    handler = AsyncMock()

    await ReviewGuideMiddleware(thread_id="t", approve_ts="").awrap_model_call(request, handler)

    sent = handler.await_args.args[0]
    assert sent.system_message.text == "base prompt\n\nwalkthrough rules"
