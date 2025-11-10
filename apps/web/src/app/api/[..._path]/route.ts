import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_INSTALLATION_NAME,
  GITHUB_INSTALLATION_ID,
  GITLAB_TOKEN_COOKIE,
  GITLAB_BASE_URL,
  GIT_PROVIDER_TYPE,
} from "@openswe/shared/constants";
import {
  getGitHubInstallationTokenOrThrow,
  getInstallationNameFromReq,
  getGitHubAccessTokenOrThrow,
} from "./utils";
import { encryptSecret } from "@openswe/shared/crypto";

// This file acts as a proxy for requests to your LangGraph server.
// Read the [Going to Production](https://github.com/langchain-ai/agent-chat-ui?tab=readme-ov-file#going-to-production) section for more information.

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
    runtime: "edge", // default
    disableWarningLog: true,
    bodyParameters: (req, body) => {
      if (body.config?.configurable && "apiKeys" in body.config.configurable) {
        const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
        if (!encryptionKey) {
          throw new Error(
            "SECRETS_ENCRYPTION_KEY environment variable is required",
          );
        }

        const apiKeys = body.config.configurable.apiKeys;
        const encryptedApiKeys: Record<string, unknown> = {};

        // Encrypt each field in the apiKeys object
        for (const [key, value] of Object.entries(apiKeys)) {
          if (typeof value === "string" && value.trim() !== "") {
            encryptedApiKeys[key] = encryptSecret(value, encryptionKey);
          } else {
            encryptedApiKeys[key] = value;
          }
        }

        // Update the body with encrypted apiKeys
        body.config.configurable.apiKeys = encryptedApiKeys;
        return body;
      }
      return body;
    },
    headers: async (req) => {
      const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
      if (!encryptionKey) {
        throw new Error(
          "SECRETS_ENCRYPTION_KEY environment variable is required",
        );
      }

      // Check which provider the user is authenticated with
      const gitlabToken = req.cookies.get(GITLAB_TOKEN_COOKIE)?.value;
      const installationIdCookie = req.cookies.get(
        GITHUB_INSTALLATION_ID_COOKIE,
      )?.value;

      // GitLab authentication
      if (gitlabToken) {
        const gitlabBaseUrl = req.cookies.get(GITLAB_BASE_URL)?.value || "https://gitlab.com";

        return {
          [GITLAB_TOKEN_COOKIE]: encryptSecret(gitlabToken, encryptionKey),
          [GITLAB_BASE_URL]: gitlabBaseUrl,
          [GIT_PROVIDER_TYPE]: "gitlab",
        } as Record<string, string>;
      }

      // GitHub authentication
      if (installationIdCookie) {
        const [installationToken, installationName] = await Promise.all([
          getGitHubInstallationTokenOrThrow(installationIdCookie, encryptionKey),
          getInstallationNameFromReq(req.clone(), installationIdCookie),
        ]);

        return {
          [GITHUB_TOKEN_COOKIE]: getGitHubAccessTokenOrThrow(req, encryptionKey),
          [GITHUB_INSTALLATION_TOKEN_COOKIE]: installationToken,
          [GITHUB_INSTALLATION_NAME]: installationName,
          [GITHUB_INSTALLATION_ID]: installationIdCookie,
          [GIT_PROVIDER_TYPE]: "github",
        } as Record<string, string>;
      }

      throw new Error(
        "No authentication found. User must authenticate with GitHub or GitLab first.",
      );
    },
  });
