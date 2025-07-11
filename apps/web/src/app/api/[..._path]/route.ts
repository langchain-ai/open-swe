import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_INSTALLATION_NAME,
} from "@open-swe/shared/constants";
import { getGitHubInstallationTokenOrThrow, getInstallationNameFromReq, getGitHubAccessTokenOrThrow } from "./utils";

// This file acts as a proxy for requests to your LangGraph server.
// Read the [Going to Production](https://github.com/langchain-ai/agent-chat-ui?tab=readme-ov-file#going-to-production) section for more information.

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
    runtime: "edge", // default
    disableWarningLog: true,
    headers: async (req) => {
      const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
      if (!encryptionKey) {
        throw new Error(
          "SECRETS_ENCRYPTION_KEY environment variable is required",
        );
      }
      const installationIdCookie = req.cookies.get(
        GITHUB_INSTALLATION_ID_COOKIE,
      )?.value;

      if (!installationIdCookie) {
        throw new Error(
          "No GitHub installation ID found. GitHub App must be installed first.",
        );
      }
      const [installationToken, installationName] = await Promise.all([
        getGitHubInstallationTokenOrThrow(installationIdCookie, encryptionKey),
        getInstallationNameFromReq(req.clone(), installationIdCookie),
      ]);

      return {
        [GITHUB_TOKEN_COOKIE]: getGitHubAccessTokenOrThrow(req, encryptionKey),
        [GITHUB_INSTALLATION_TOKEN_COOKIE]: installationToken,
        [GITHUB_INSTALLATION_NAME]: installationName,
      };
    },
  });
