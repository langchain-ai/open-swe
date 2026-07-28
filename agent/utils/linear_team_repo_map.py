from typing import Any

# SPEEDBAY DEVIATION: upstream ships its own workspace's team mapping here, and
# our Linear team "Open SWE" collided with theirs (routing to langchain-ai/open-swe,
# which the allowlist then rejected). Docs designate this file as deployer config.
# Empty mapping = every team falls back to DEFAULT_REPO_OWNER/DEFAULT_REPO_NAME
# (speedbay/warehouse). Per-comment `repo:owner/name` still overrides.
LINEAR_TEAM_TO_REPO: dict[str, dict[str, Any] | dict[str, str]] = {}
