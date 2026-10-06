from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import ModelCallLimitMiddleware
from managed_deepagents import define_deep_agent
from middleware.open_swe import OpenSweContext, OpenSweMiddleware

agent = define_deep_agent(
    name="open-swe",
    model="openai:gpt-6.1-sol",
    context_schema=OpenSweContext,
    middleware=[
        OpenSweMiddleware(),
        ModelCallLimitMiddleware(run_limit=5000, exit_behavior="end"),
    ],
    subagents=[
        {
            "name": GENERAL_PURPOSE_SUBAGENT["name"],
            "description": GENERAL_PURPOSE_SUBAGENT["description"],
            "mode": "fork",
        }
    ],
)
