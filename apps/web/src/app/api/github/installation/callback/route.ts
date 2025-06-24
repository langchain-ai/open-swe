import { GITHUB_INSTALLATION_ID_COOKIE } from "@open-swe/shared/constants";
import { NextRequest, NextResponse } from "next/server";

/**
 * Handles the callback from GitHub App installation
 * After a user installs the app and selects repositories, GitHub redirects here
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const installationId = searchParams.get("installation_id");

    if (!installationId) {
      return NextResponse.redirect(
        new URL("/?error=missing_installation_id", request.url),
      );
    }

    // Store the installation ID in a cookie for future API calls
    const response = NextResponse.redirect(
      new URL("/?installation=success", request.url),
    );

    response.cookies.set(GITHUB_INSTALLATION_ID_COOKIE, installationId, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: "/",
    });

    return response;
  } catch (error) {
    console.error("GitHub App installation callback error:", error);
    return NextResponse.redirect(
      new URL("/?error=installation_callback_failed", request.url),
    );
  }
}
