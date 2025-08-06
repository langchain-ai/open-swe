import { NextRequest, NextResponse } from "next/server";
import { getInstallationToken } from "@open-swe/shared/github/auth";
import { getInstallationRepositories, Repository } from "@/utils/github";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";

/**
 * Fetches repositories accessible to the GitHub App installation
 * Requires a valid GitHub installation ID in the cookies. Supports pagination via 'page' query parameter.
 */
export async function GET(request: NextRequest) {
  try {
    // Get the installation ID from cookies
    const installationId = request.cookies.get(
      GITHUB_INSTALLATION_ID_COOKIE,
    )?.value;

    if (!installationId) {
      return NextResponse.json(
        {
          error:
            "GitHub installation ID not found. Please install the app first.",
        },
        { status: 401 },
      );
    }

    // Get pagination parameters from query string
    const { searchParams } = new URL(request.url);
    const page = parseInt(searchParams.get("page") || "1", 10);
    const perPage = 30; // Fixed at 30 repositories per page

    // Validate page parameter
    if (page < 1 || isNaN(page)) {
      return NextResponse.json(
        { error: "Invalid page parameter. Must be a positive integer." },
        { status: 400 },
      );
    }

    // Get GitHub App credentials from environment variables
    const appId = process.env.GITHUB_APP_ID;
    const privateKey = process.env.GITHUB_APP_PRIVATE_KEY?.replace(
      /\\n/g,
      "\n",
    );

    if (!appId || !privateKey) {
      return NextResponse.json(
        { error: "GitHub App configuration missing" },
        { status: 500 },
      );
    }

    // Get an installation access token
    let installationToken: string;
    try {
      installationToken = await getInstallationToken(
        installationId,
        appId,
        privateKey,
      );
    } catch (error) {
      console.error("Failed to get installation token:", error);
      return NextResponse.json(
        { error: "Failed to get installation token" },
        { status: 401 },
      );
    }

    // Fetch repositories accessible to this installation
    let repositoryData: {
      repositories: Repository[];
      hasMore: boolean;
      totalCount: number;
    };
    try {
      repositoryData = await getInstallationRepositories(
        installationToken,
        page,
        perPage,
      );
    } catch (error) {
      console.error("Failed to fetch repositories:", error);
      return NextResponse.json(
        { error: "Failed to fetch repositories" },
        { status: 500 },
      );
    }

    // Transform the response to include only the data we need
    const transformedRepos = repositoryData.repositories.map((repo) => ({
      id: repo.id,
      name: repo.name,
      full_name: repo.full_name,
      description: repo.description,
      private: repo.private,
      html_url: repo.html_url,
      default_branch: repo.default_branch,
      permissions: repo.permissions,
      fork: repo.fork,
      has_issues: repo.has_issues,
    }));

    return NextResponse.json({
      repositories: transformedRepos,
      pagination: {
        page,
        perPage,
        hasMore: repositoryData.hasMore,
        totalCount: repositoryData.totalCount,
      },
    });
  } catch (error) {
    console.error("Error fetching GitHub repositories:", error);
    return NextResponse.json(
      { error: "Failed to fetch repositories" },
      { status: 500 },
    );
  }
}
