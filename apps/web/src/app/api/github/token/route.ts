import { NextRequest, NextResponse } from "next/server";
import { getInstallationToken } from "@open-swe/shared/github/auth";
import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";

/**
 * Returns a GitHub installation token that can be used for Git operations
 * This endpoint is intended for internal use by the AI coding agent
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
    try {
      const installationToken = await getInstallationToken(
        installationId,
        appId,
        privateKey,
      );

      return NextResponse.json({
        token: installationToken,
        installation_id: installationId,
      });
    } catch (error) {
      console.error("Failed to get installation token:", error);
      return NextResponse.json(
        { error: "Failed to get installation token" },
        { status: 401 },
      );
    }
  } catch (error) {
    console.error("Error generating GitHub token:", error);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 },
    );
  }
}
