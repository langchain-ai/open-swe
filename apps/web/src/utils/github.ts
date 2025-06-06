import * as jwt from "jsonwebtoken";

/**
 * Generates a JWT for GitHub App authentication
 */
export function generateJWT(appId: string, privateKey: string): string {
  const now = Math.floor(Date.now() / 1000);

  const payload = {
    iat: now,
    exp: now + 10 * 60,
    iss: appId,
  };

  return jwt.sign(payload, privateKey, { algorithm: "RS256" });
}

/**
 * Gets an installation access token for a GitHub App installation
 */
export async function getInstallationToken(
  installationId: string,
  appId: string,
  privateKey: string,
): Promise<string> {
  const jwtToken = generateJWT(appId, privateKey);

  const response = await fetch(
    `https://api.github.com/app/installations/${installationId}/access_tokens`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${jwtToken}`,
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "YourAppName",
      },
    },
  );

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(
      `Failed to get installation token: ${JSON.stringify(errorData)}`,
    );
  }

  const data = await response.json();
  return data.token;
}

/**
 * Fetches repositories accessible to a GitHub App installation
 */
export async function getInstallationRepositories(
  installationToken: string,
): Promise<Repository[]> {
  const response = await fetch(
    "https://api.github.com/installation/repositories",
    {
      headers: {
        Authorization: `Bearer ${installationToken}`,
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "YourAppName",
      },
    },
  );

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(
      `Failed to fetch repositories: ${JSON.stringify(errorData)}`,
    );
  }

  const data = await response.json();
  return data.repositories;
}

/**
 * Repository interface representing GitHub repository data
 */
export interface Repository {
  id: number;
  name: string;
  full_name: string;
  description: string | null;
  private: boolean;
  html_url: string;
  default_branch: string;
  permissions: {
    admin: boolean;
    maintain: boolean;
    push: boolean;
    triage: boolean;
    pull: boolean;
  };
}
