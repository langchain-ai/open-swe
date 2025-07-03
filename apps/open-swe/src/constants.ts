import { SNAPSHOT_NAME } from "@open-swe/shared/constants";
import { CreateSandboxParams } from "@daytonaio/sdk";

export const DEFAULT_SANDBOX_CREATE_PARAMS: CreateSandboxParams = {
  resources: {
    cpu: 2,
    memory: 4,
    disk: 5,
  },
  user: "daytona",
  image: SNAPSHOT_NAME,
};

export const MCP_SERVERS = {
  "langgraph-docs-mcp": {
    "command": "uvx",
    "args": [
      "--from",
      "mcpdoc",
      "mcpdoc",
      "--urls",
      "LangGraphPY:https://langchain-ai.github.io/langgraph/llms.txt LangGraphJS:https://langchain-ai.github.io/langgraphjs/llms.txt",
      "--transport",
      "stdio"
    ]
  }
}
