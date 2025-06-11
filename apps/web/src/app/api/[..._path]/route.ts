// import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_TOKEN_COOKIE,
} from "@/lib/auth";
import { initApiPassthrough } from "./passthrough";

// This file acts as a proxy for requests to your LangGraph server.
// Read the [Going to Production](https://github.com/langchain-ai/agent-chat-ui?tab=readme-ov-file#going-to-production) section for more information.

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL,
    // apiKey: process.env.LANGSMITH_API_KEY
    runtime: "edge", // default
    disableWarningLog: true,
    modifyRequestHeaders: (req) => {
      const installationToken = req.cookies.get(
        GITHUB_INSTALLATION_TOKEN_COOKIE,
      );
      const accessToken = req.cookies.get(GITHUB_TOKEN_COOKIE);
      console.log({
        [GITHUB_INSTALLATION_TOKEN_COOKIE]: installationToken?.value ?? "",
        [GITHUB_TOKEN_COOKIE]: accessToken?.value ?? "",
      });
      return {
        [GITHUB_INSTALLATION_TOKEN_COOKIE]: installationToken?.value ?? "",
        [GITHUB_TOKEN_COOKIE]: accessToken?.value ?? "",
      };
    },
  });
