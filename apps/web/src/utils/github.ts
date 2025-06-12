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
        "User-Agent": "OpenSWE-Agent",
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
  page: number = 1,
  perPage: number = 30,
): Promise<{
  repositories: Repository[];
  hasMore: boolean;
  totalCount: number;
}> {
  const url = new URL("https://api.github.com/installation/repositories");
  url.searchParams.set("page", page.toString());
  url.searchParams.set("per_page", perPage.toString());

  const response = await fetch(url.toString(), {
    headers: {
      Authorization: `Bearer ${installationToken}`,
      Accept: "application/vnd.github.v3+json",
      "User-Agent": "YourAppName",
    },
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(
      `Failed to fetch repositories: ${JSON.stringify(errorData)}`,
    );
  }

  const data = await response.json();

  return {
    repositories: data.repositories,
    hasMore: data.repositories.length === perPage,
    totalCount: data.total_count,
  };
}

/**
 * Fetches branches for a specific repository using OAuth access token
 */
export async function getRepositoryBranches(
  owner: string,
  repo: string,
  page: number = 1,
  perPage: number = 30,
): Promise<{ branches: Branch[]; hasMore: boolean; totalCount?: number }> {
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

  // Fetch first 30 branches only
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
  const branches: Branch[] = Array.isArray(data) ? data : [];

  if (branches.length === 0) {
    return { branches: [], hasMore: false };
  }

  // Ensure default branch is at the beginning if it exists
  if (defaultBranch && page === 1) {
    const defaultBranchIndex = branches.findIndex(
      (branch) => branch.name === defaultBranch,
    );
    if (defaultBranchIndex > 0) {
      const defaultBranchData = branches[defaultBranchIndex];
      branches.splice(defaultBranchIndex, 1);
      branches.unshift(defaultBranchData);
    } else {
      // Need to fetch default branch
      const defaultBranchResponse = await fetch(
        `${getBaseApiUrl()}github/proxy/repos/${owner}/${repo}/branches/${defaultBranch}`,
        {
          headers: {
            Accept: "application/vnd.github.v3+json",
            "User-Agent": "OpenSWE-Agent",
          },
        },
      );
      if (!defaultBranchResponse.ok) {
        const errorData = await defaultBranchResponse.json();
        throw new Error(
          `Failed to fetch default branch: ${JSON.stringify(errorData)}`,
        );
      }
      const defaultBranchData = await defaultBranchResponse.json();
      branches.unshift(defaultBranchData);
    }
  }

  return {
    branches,
    hasMore: branches.length >= perPage,
    totalCount: branches.length,
  };
}

/**
 * Fetches a specific repository using OAuth access token
 */
export async function getRepository(owner: string, repo: string) {
  const response = await fetch(
    `${getBaseApiUrl()}github/proxy/repos/${owner}/${repo}`,
    {
      headers: {
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "OpenSWE-Agent",
      },
    },
  );

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(`Failed to fetch repository: ${JSON.stringify(errorData)}`);
  }

  const data = await response.json();
  return data;
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

/**
 * Fetches a pull request for a specific repository using OAuth access token
 */
export async function getPullRequest(inputs: {
  owner: string;
  repo: string;
  baseBranch: string;
  headBranch: string;
}) {
  try {
    const queryParams = new URLSearchParams();
    queryParams.set("base", inputs.baseBranch);
    queryParams.set("head", inputs.headBranch);
    queryParams.set("state", "all");
    const response = await fetch(
      `${getBaseApiUrl()}github/proxy/repos/${inputs.owner}/${inputs.repo}/pulls?${queryParams.toString()}`,
      {
        headers: {
          Accept: "application/vnd.github.v3+json",
          "User-Agent": "OpenSWE-Agent",
        },
      },
    );

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(
        `Failed to fetch pull request: ${JSON.stringify(errorData)}`,
      );
    }

    const data = await response.json();
    if (data && Array.isArray(data) && data.length > 0) {
      return data.filter((d) => d.head?.ref === inputs.headBranch)[0];
    }
    return data?.[0];
  } catch (e) {
    console.error("Failed to get pull request", {
      inputs,
      error: e,
    });
    return null;
  }
}
