import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";
import * as jwt from "jsonwebtoken";

function getBaseApiUrl(): string {
  let baseApiUrl = new URL(
    process.env.NEXT_PUBLIC_API_URL || "http://localhost:3000/api",
  ).href;
  baseApiUrl = baseApiUrl.endsWith("/") ? baseApiUrl : `${baseApiUrl}/`;
  return baseApiUrl;
}

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
 * Fetches branches for a specific repository using OAuth access token
 */
export async function getRepositoryBranches(
  owner: string,
  repo: string,
): Promise<Branch[]> {
  const allBranches: Branch[] = [];
  let page = 1;
  const perPage = 100; // Maximum allowed by GitHub API

  // First, get repository info to ensure we have the default branch

  const repoResponse = await fetch(
    `${getBaseApiUrl()}github/proxy/repos/${owner}/${repo}`,
    {
      headers: {
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "OpenSWE-Agent",
      },
    },
  );

  let defaultBranch: string | null = null;
  if (repoResponse.ok) {
    const repoData = await repoResponse.json();
    defaultBranch = repoData.default_branch;
  }

  // Fetch all branches with pagination
  while (true) {
    const response = await fetch(
      `${getBaseApiUrl()}github/proxy/repos/${owner}/${repo}/branches?per_page=${perPage}&page=${page}`,
      {
        headers: {
          Accept: "application/vnd.github.v3+json",
          "User-Agent": "OpenSWE-Agent",
        },
      },
    );

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(`Failed to fetch branches: ${JSON.stringify(errorData)}`);
    }

    const data = await response.json();

    if (!Array.isArray(data) || data.length === 0) {
      break;
    }

    allBranches.push(...data);

    // If we got less than the requested amount, we've reached the end
    if (data.length < perPage) {
      break;
    }

    page++;
  }

  // Ensure default branch is at the beginning if it exists
  if (defaultBranch) {
    const defaultBranchIndex = allBranches.findIndex(
      (branch) => branch.name === defaultBranch,
    );
    if (defaultBranchIndex > 0) {
      const defaultBranchData = allBranches[defaultBranchIndex];
      allBranches.splice(defaultBranchIndex, 1);
      allBranches.unshift(defaultBranchData);
    }
  }

  return allBranches;
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

/**
 * Branch interface representing GitHub branch data
 */
export interface Branch {
  name: string;
  commit: {
    sha: string;
    url: string;
  };
  protected: boolean;
}

// Helper function to get GitHub OAuth access token from cookies
export function getGitHubAccessToken(): string | null {
  if (typeof document === "undefined") return null;

  const cookies = document.cookie.split("; ");
  const tokenCookie = cookies.find((row) =>
    row.startsWith(`${GITHUB_TOKEN_COOKIE}=`),
  );
  return tokenCookie ? tokenCookie.split("=")[1] : null;
}
