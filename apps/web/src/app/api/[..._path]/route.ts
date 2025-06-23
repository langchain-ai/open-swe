import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
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
  req: NextRequest,
  encryptionKey: string,
): Promise<string> {
  const installationIdCookie = req.cookies.get(
    GITHUB_INSTALLATION_ID_COOKIE,
  )?.value;

  if (!installationIdCookie) {
    throw new Error(
      "No GitHub installation ID found. GitHub App must be installed first.",
    );
  }

  const appId = process.env.GITHUB_APP_ID;
  const privateAppKey = process.env.GITHUB_APP_PRIVATE_KEY?.replace(
    /\\n/g,
    "\n",
  );

  if (!appId || !privateAppKey) {
    throw new Error("GitHub App ID or Private App Key is not configured.");
  }

  try {
    const token = await getInstallationToken(
      installationIdCookie,
      appId,
      privateAppKey,
    );
    return encryptGitHubToken(token, encryptionKey);
  } catch (error) {
    console.error("Failed to get GitHub installation token:", error);
    throw new Error(
      "Failed to get GitHub installation token. The GitHub App may need to be reinstalled.",
    );
  }
}

// This file acts as a proxy for requests to your LangGraph server.
// It automatically injects GitHub authentication headers from secure HTTP-only cookies.
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

      try {
        // Debug: Log available cookies
        const allCookies = req.cookies.getAll();
        console.log(
          "Available cookies:",
          allCookies.map((c) => ({ name: c.name, hasValue: !!c.value })),
        );

        const gitHubToken = req.cookies.get(GITHUB_TOKEN_COOKIE)?.value;
        const installationId = req.cookies.get(
          GITHUB_INSTALLATION_ID_COOKIE,
        )?.value;

        console.log("GitHub access token present:", !!gitHubToken);
        console.log("Installation ID present:", !!installationId);

        const headers = {
          [GITHUB_TOKEN_COOKIE]: getGitHubAccessTokenOrThrow(
            req,
            encryptionKey,
          ),
          [GITHUB_INSTALLATION_TOKEN_COOKIE]:
            await getGitHubInstallationTokenOrThrow(req, encryptionKey),
        };

        console.log("Headers being sent to LangGraph:", Object.keys(headers));
        return headers;
      } catch (error) {
        console.error("Authentication error in API proxy:", error);
        throw error; // This will cause the request to fail with proper error messaging
      }
    },
    bodyParameters: async (req, body) => {
      const encryptionKey = process.env.GITHUB_TOKEN_ENCRYPTION_KEY;
      if (!encryptionKey) {
        return body; // Return unchanged if no encryption key
      }

      try {
        const configurable = {
          [GITHUB_TOKEN_COOKIE]: getGitHubAccessTokenOrThrow(
            req,
            encryptionKey,
          ),
          [GITHUB_INSTALLATION_TOKEN_COOKIE]:
            await getGitHubInstallationTokenOrThrow(req, encryptionKey),
        };

        // Inject configurable into request body
        const modifiedBody = {
          ...body,
          configurable: {
            ...body?.configurable,
            ...configurable,
          },
        };

        console.log(
          "Adding to request body configurable:",
          Object.keys(configurable),
        );
        return modifiedBody;
      } catch (error) {
        console.error("Configuration error in API proxy:", error);
        return body; // Return unchanged body on error
      }
    },
  });
