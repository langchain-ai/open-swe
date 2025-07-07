import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE_X_PREFIX,
} from "@open-swe/shared/constants";
import { encryptGitHubToken } from "@open-swe/shared/crypto";
import { NextRequest } from "next/server";
import { getInstallationToken } from "@/utils/github";

function getGitHubAccessTokenOrThrow(
  req: NextRequest,
  encryptionKey: string,
): string {
  const token = req.cookies.get(GITHUB_TOKEN_COOKIE)?.value ?? "";

  if (!token) {
    throw new Error(
      "No GitHub access token found. User must authenticate first.",
    );
  }

  return encryptGitHubToken(token, encryptionKey);
}

async function getGitHubInstallationTokenOrThrow(
  installationIdCookie: string,
  encryptionKey: string,
): Promise<string> {
  const appId = process.env.GITHUB_APP_ID;
  const privateAppKey = process.env.GITHUB_APP_PRIVATE_KEY;

  if (!appId || !privateAppKey) {
    throw new Error("GitHub App ID or Private App Key is not configured.");
  }

  const token = await getInstallationToken(
    installationIdCookie,
    appId,
    privateAppKey,
  );
  return encryptGitHubToken(token, encryptionKey);
}

// This file acts as a proxy for requests to your LangGraph server.
// Read the [Going to Production](https://github.com/langchain-ai/agent-chat-ui?tab=readme-ov-file#going-to-production) section for more information.

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
    runtime: "edge", // default
    disableWarningLog: true,
    headers: async (req) => {
      const encryptionKey = process.env.GITHUB_TOKEN_ENCRYPTION_KEY;
      if (!encryptionKey) {
        throw new Error(
          "GITHUB_TOKEN_ENCRYPTION_KEY environment variable is required",
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

      return {
        [GITHUB_TOKEN_COOKIE]: getGitHubAccessTokenOrThrow(req, encryptionKey),
        [GITHUB_INSTALLATION_TOKEN_COOKIE]:
          await getGitHubInstallationTokenOrThrow(
            installationIdCookie,
            encryptionKey,
          ),
        [GITHUB_INSTALLATION_ID_COOKIE_X_PREFIX]: installationIdCookie,
      };
    },
  });
