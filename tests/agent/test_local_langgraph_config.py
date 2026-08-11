import json
from pathlib import Path


def test_desktop_langgraph_config() -> None:
    config_path = Path(__file__).parents[2] / "langgraph.desktop.json"
    config = json.loads(config_path.read_text())
    assert config["graphs"] == {"local_agent": "agent.graphs.local_agent:get_local_agent"}
    assert config["auth"] == {
        "path": "agent.local_auth:auth",
        "disable_studio_auth": True,
    }
    assert config["http"] == {"disable_ui": True}
    assert "app" not in config["http"]
