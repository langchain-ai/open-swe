"""Replace Deep Agents' delegation middleware without registering any tools."""

from langchain.agents.middleware.types import AgentMiddleware


class NoSubagentsMiddleware(AgentMiddleware):
    @property
    def name(self) -> str:
        return "SubAgentMiddleware"
