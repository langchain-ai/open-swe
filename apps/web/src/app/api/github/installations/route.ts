import { NextRequest, NextResponse } from "next/server";
import { getGitHubToken } from "@/lib/auth";
import { Endpoints } from "@octokit/types";

type GitHubInstallationsResponse =
  Endpoints["GET /user/installations"]["response"]["data"];

/**
 * Fetches all GitHub App installations accessible to the current user
 * Uses the user's access token from GITHUB_TOKEN_COOKIE to call GET /user/installations
 */
export async function GET(request: NextRequest) {
  try {
    // Get the user's access token from cookies
    const tokenData = getGitHubToken(request);

    if (!tokenData || !tokenData.access_token) {
      return NextResponse.json(
        {
          error: "GitHub access token not found. Please authenticate first.",
        },
        { status: 401 },
      );
    }

    // Fetch installations from GitHub API
    const response = await fetch("https://api.github.com/user/installations", {
      headers: {
        Authorization: `${tokenData.token_type} ${tokenData.access_token}`,
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "OpenSWE-Agent",
      },
    });

    if (!response.ok) {
      const errorData = await response.json();
      return NextResponse.json(
        {
          error: `Failed to fetch installations: ${JSON.stringify(errorData)}`,
        },
        { status: response.status },
      );
    }

    const data: GitHubInstallationsResponse = await response.json();

    return NextResponse.json(data);
  } catch (error) {
    console.error("Error fetching GitHub installations:", error);
    return NextResponse.json(
      { error: "Failed to fetch installations" },
      { status: 500 },
    );
  }
}
