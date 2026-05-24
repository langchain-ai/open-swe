from typing import Any

LINEAR_TEAM_TO_REPO: dict[str, dict[str, Any] | dict[str, str]] = {
    "Blank Metal": {
        "projects": {
            "AI News Agent": {"owner": "BlankMetal", "name": "ai-news-agent"},
        },
    },
    "Shippy": {
        "projects": {
            "Shippy - Core": {"owner": "BlankMetal", "name": "shippy-pricing-tool-temp-replatform"},
        },
        "default": {"owner": "BlankMetal", "name": "shippy-pricing-tool-temp-replatform"},
    },
}
