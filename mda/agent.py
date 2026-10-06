from langchain.agents.middleware import ModelCallLimitMiddleware
from managed_deepagents import define_deep_agent

agent = define_deep_agent(
    name="open-swe",
    model="openai:gpt-6.1-sol",
    middleware=[ModelCallLimitMiddleware(run_limit=1000, exit_behavior="end")],
)
