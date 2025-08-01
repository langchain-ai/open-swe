export const TIMEOUT_SEC = 60; // 1 minute
export const SANDBOX_ROOT_DIR = "/home/daytona";
export const DAYTONA_IMAGE_NAME = "daytonaio/langchain-open-swe:0.1.0";
export const DAYTONA_SNAPSHOT_NAME = "open-swe-vcpu2-mem4-disk5";
export const PLAN_INTERRUPT_DELIMITER = ":::";
export const PLAN_INTERRUPT_ACTION_TITLE = "Approve/Edit Plan";

// Prefix the access token with `x-` so that it's included in requests to the LangGraph server.
export const GITHUB_TOKEN_COOKIE = "x-github-access-token";
export const GITHUB_INSTALLATION_TOKEN_COOKIE = "x-github-installation-token";
export const GITHUB_INSTALLATION_NAME = "x-github-installation-name";
export const GITHUB_PAT = "x-github-pat";
export const GITHUB_INSTALLATION_ID = "x-github-installation-id";
export const LOCAL_MODE_HEADER = "x-local-mode";
export const DO_NOT_RENDER_ID_PREFIX = "do-not-render-";
export const GITHUB_AUTH_STATE_COOKIE = "github_auth_state";
export const GITHUB_INSTALLATION_ID_COOKIE = "github_installation_id";
export const GITHUB_TOKEN_TYPE_COOKIE = "github_token_type";

export const MANAGER_GRAPH_ID = "manager";
export const PLANNER_GRAPH_ID = "planner";
export const PROGRAMMER_GRAPH_ID = "programmer";

export const GITHUB_USER_ID_HEADER = "x-github-user-id";
export const GITHUB_USER_LOGIN_HEADER = "x-github-user-login";

export const DEFAULT_MCP_SERVERS = {
  "langgraph-docs-mcp": {
    command: "uvx",
    args: [
      "--from",
      "mcpdoc",
      "mcpdoc",
      "--urls",
      "LangGraphPY:https://langchain-ai.github.io/langgraph/llms.txt LangGraphJS:https://langchain-ai.github.io/langgraphjs/llms.txt",
      "--transport",
      "stdio",
    ],
    stderr: "inherit" as const,
  },
};

export const API_KEY_REQUIRED_MESSAGE =
  "Unknown users must provide API keys to use the Open SWE demo application";

export const OPEN_SWE_STREAM_MODE = [
  "values",
  "updates",
  "messages",
  "messages-tuple",
  "custom",
];
