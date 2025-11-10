import { NextRequest, NextResponse } from "next/server";
import { getGitHubToken, getInstallationCookieOptions } from "@/lib/auth";
import { Endpoints } from "@octokit/types";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@openswe/shared/constants";

type GitHubInstallationsResponse =
  Endpoints["GET /user/installations"]["response"]["data"];

/**
 * Fetches all GitHub App installations accessible to the current user
 * Uses the user's access token from GITHUB_TOKEN_COOKIE to call GET /user/installations
 * Also automatically sets the installation ID cookie if it's missing
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

    // If there's at least one installation and the installation ID cookie is missing,
    // automatically set it to the first installation
    const existingInstallationId = request.cookies.get(
      GITHUB_INSTALLATION_ID_COOKIE,
    )?.value;

    const responseWithCookie = NextResponse.json(data);

    if (!existingInstallationId && data.installations && data.installations.length > 0) {
      const firstInstallationId = data.installations[0].id.toString();
      responseWithCookie.cookies.set(
        GITHUB_INSTALLATION_ID_COOKIE,
        firstInstallationId,
        getInstallationCookieOptions(),
      );
    }

    return responseWithCookie;
  } catch (error) {
    console.error("Error fetching GitHub installations:", error);
    return NextResponse.json(
      { error: "Failed to fetch installations" },
      { status: 500 },
    );
  }
}
