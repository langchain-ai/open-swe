from typing import Any

LINEAR_TEAM_TO_REPO: dict[str, dict[str, Any] | dict[str, str]] = {
    "Brace's test workspace": {"owner": "langchain-ai", "name": "open-swe"},
    "Yogesh-dev": {
        "projects": {
            "open-swe-v3-test": {"owner": "aran-yogesh", "name": "nimedge"},
            "open-swe-dev-test": {"owner": "aran-yogesh", "name": "TalkBack"},
        },
        "default": {
            "owner": "aran-yogesh",
            "name": "TalkBack",
        },  # Fallback for issues without project
    },
    "LangChain OSS": {
        "projects": {
            "deepagents": {"owner": "langchain-ai", "name": "deepagents"},
            "langchain": {"owner": "langchain-ai", "name": "langchain"},
        }
    },
    "Applied AI": {
        "projects": {
            "GTM Engineering": {"owner": "langchain-ai", "name": "ai-sdr"},
        },
        "default": {"owner": "langchain-ai", "name": "ai-sdr"},
    },
    "Docs": {"default": {"owner": "langchain-ai", "name": "docs"}},
    "Open SWE": {"default": {"owner": "langchain-ai", "name": "open-swe"}},
    "LangSmith Deployment": {"default": {"owner": "langchain-ai", "name": "langgraph-api"}},
}
