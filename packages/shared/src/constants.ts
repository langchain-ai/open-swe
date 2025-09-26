import {
  getLocalWorkingDirectory,
  isLocalModeFromEnv,
} from "./open-swe/local-mode.js";

export const TIMEOUT_SEC = 900; // 15 minutes
const CONTAINER_PROJECT_ROOT = "/workspace/project";

export const SANDBOX_ROOT_DIR =
  process.env.SANDBOX_ROOT_DIR ??
  (isLocalModeFromEnv() ? getLocalWorkingDirectory() : CONTAINER_PROJECT_ROOT);
export const PLAN_INTERRUPT_DELIMITER = ":::";
export const PLAN_INTERRUPT_ACTION_TITLE = "Approve/Edit Plan";
export const LOCAL_MODE_HEADER = "x-local-mode";
export const DO_NOT_RENDER_ID_PREFIX = "do-not-render-";
export const SESSION_COOKIE = "session";

export const OPEN_SWE_V2_GRAPH_ID = "open-swe-v2";
export const MANAGER_GRAPH_ID = "manager";
export const PLANNER_GRAPH_ID = "planner";
export const PROGRAMMER_GRAPH_ID = "programmer";

export const DEFAULT_MCP_SERVERS = {
  "langgraph-docs-mcp": {
    command: "uvx",
    args: [
      "--from",
      "mcpdoc",
      "mcpdoc",
      "--urls",
      "LangGraphPY:https://docs.langchain.com/langgraph/llms.txt LangGraphJS:https://docs.langchain.com/langgraphjs/llms.txt",
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
