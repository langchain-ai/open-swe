import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";

// This file acts as a proxy for requests to your LangGraph server.
// Read the [Going to Production](https://github.com/langchain-ai/agent-chat-ui?tab=readme-ov-file#going-to-production) section for more information.

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL,
    // apiKey: process.env.LANGSMITH_API_KEY
    runtime: "edge", // default
    disableWarningLog: true,
    headers: (req) => {
      return {
        [GITHUB_TOKEN_COOKIE]:
          req.cookies.get(GITHUB_TOKEN_COOKIE)?.value ?? "",
      };
    },
  });
