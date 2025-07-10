import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_INSTALLATION_NAME,
} from "@open-swe/shared/constants";
import { encryptGitHubToken } from "@open-swe/shared/crypto";
import { NextRequest } from "next/server";
import { getInstallationToken } from "@/utils/github";
import { App } from "@octokit/app";
import { validate } from "uuid";

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

async function getInstallationName(installationId: string) {
  if (!process.env.GITHUB_APP_ID || !process.env.GITHUB_APP_PRIVATE_KEY) {
    throw new Error("GitHub App ID or Private App Key is not configured.");
  }
  const app = new App({
    appId: process.env.GITHUB_APP_ID,
    privateKey: process.env.GITHUB_APP_PRIVATE_KEY,
  });

  // Get installation details
  const { data } = await app.octokit.request(
    "GET /app/installations/{installation_id}",
    {
      installation_id: Number(installationId),
    },
  );

  const installationName =
    data.account && "name" in data.account
      ? data.account.name
      : data.account?.login;

  return installationName ?? "";
}

const isNewRunRequest = (reqUrlStr: string, reqMethod: string) => {
  try {
    const reqPathnameParts = new URL(reqUrlStr).pathname.split("/");
    const isCreateNewRunReq =
      reqPathnameParts?.[1] === "api" &&
      reqPathnameParts?.[2] === "threads" &&
      validate(reqPathnameParts?.[3]) &&
      reqPathnameParts?.[4] === "runs" &&
      reqPathnameParts.length === 5 &&
      reqMethod.toLowerCase() === "post";
    const isStreamRunReq =
      reqPathnameParts?.[1] === "api" &&
      reqPathnameParts?.[2] === "threads" &&
      validate(reqPathnameParts?.[3]) &&
      reqPathnameParts?.[4] === "runs" &&
      validate(reqPathnameParts?.[5]) &&
      reqPathnameParts?.[6]?.startsWith("stream") &&
      reqMethod.toLowerCase() === "get";
    return isCreateNewRunReq || isStreamRunReq;
  } catch {
    return false;
  }
};

const isGetStateRequest = (reqUrlStr: string, reqMethod: string) => {
  try {
    const reqPathnameParts = new URL(reqUrlStr).pathname.split("/");
    const isGetStateReq =
      reqPathnameParts?.[1] === "api" &&
      reqPathnameParts?.[2] === "threads" &&
      validate(reqPathnameParts?.[3]) &&
      reqPathnameParts?.[4] === "state" &&
      reqMethod.toLowerCase() === "get";
    return isGetStateReq;
  } catch {
    return false;
  }
};

const isSearchThreadsRequest = (reqUrlStr: string, reqMethod: string) => {
  try {
    const reqPathnameParts = new URL(reqUrlStr).pathname.split("/");
    const isGetStateReq =
      reqPathnameParts?.[1] === "api" &&
      reqPathnameParts?.[2] === "threads" &&
      reqPathnameParts?.[3] === "search" &&
      reqMethod.toLowerCase() === "post";
    return isGetStateReq;
  } catch {
    return false;
  }
};

async function getInstallationNameFromReq(
  req: Request,
  installationId: string,
): Promise<string> {
  try {
    const requestJson = await req.json();
    const installationName = requestJson?.input?.targetRepository?.owner;
    if (installationName) {
      return installationName;
    }
  } catch {
    // no-op
  }

  try {
    if (
      isNewRunRequest(req.url, req.method) ||
      isGetStateRequest(req.url, req.method) ||
      isSearchThreadsRequest(req.url, req.method)
    ) {
      return await getInstallationName(installationId);
    }
    return "";
  } catch {
    return "";
  }
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
