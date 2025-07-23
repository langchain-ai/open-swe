import { getInstallationToken } from "@open-swe/shared/github/auth";
import { App } from "@octokit/app";
import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";
import { encryptSecret } from "@open-swe/shared/crypto";
import { NextRequest } from "next/server";

export function getGitHubAccessTokenOrThrow(
  req: NextRequest,
  encryptionKey: string,
): string {
  const token = req.cookies.get(GITHUB_TOKEN_COOKIE)?.value ?? "";

  if (!token) {
    throw new Error(
      "No GitHub access token found. User must authenticate first.",
    );
  }

  return encryptSecret(token, encryptionKey);
}

export async function getGitHubInstallationTokenOrThrow(
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
  return encryptSecret(token, encryptionKey);
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

export async function getInstallationNameFromReq(
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
    return await getInstallationName(installationId);
  } catch {
    return "";
  }
}
