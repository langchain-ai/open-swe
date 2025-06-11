import {
  GITHUB_INSTALLATION_RETURN_TO_COOKIE,
  GITHUB_INSTALLATION_STATE_COOKIE,
} from "@/lib/auth";
import { NextRequest, NextResponse } from "next/server";
import { randomBytes } from "crypto";
import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";

/**
 * Initiates the GitHub App installation flow
 * This redirects users to the GitHub App installation page where they can
 * select which repositories to grant access to
 */
export async function GET(request: NextRequest) {
  try {
    const accessToken = request.cookies.get(GITHUB_TOKEN_COOKIE)?.value;

    if (!accessToken) {
      return NextResponse.json(
        { error: "GitHub access token not found" },
        { status: 401 },
      );
    }

    // Get GitHub App name from environment variables
    const githubAppName = process.env.GITHUB_APP_NAME;

    if (!githubAppName) {
      return NextResponse.json(
        { error: "GitHub App name not configured" },
        { status: 500 },
      );
    }

    // Check for existing state or generate a new one
    let state = request.cookies.get(GITHUB_INSTALLATION_STATE_COOKIE)?.value;

    // If no state exists or we want to ensure a fresh state, generate a new one
    if (!state) {
      state = randomBytes(16).toString("hex");
    }

    // Create a response that will redirect to the GitHub App installation page
    // Include the callback URL as a parameter to ensure GitHub redirects back to our app
    // Add the state as a custom parameter in the callback URL
    const baseCallbackUrl = `${request.nextUrl.origin}/api/github/installation-callback`;
    const callbackUrl = `${baseCallbackUrl}?custom_state=${encodeURIComponent(state)}`;
    const response = NextResponse.redirect(
      `https://github.com/apps/${githubAppName}/installations/new?redirect_uri=${encodeURIComponent(callbackUrl)}`,
    );

    // Cookie options for security and proper expiration
    const cookieOptions = {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax" as const,
      maxAge: 60 * 10, // 10 minutes
      path: "/",
    };

    // Store the state in a cookie for validation when GitHub redirects back
    response.cookies.set(
      GITHUB_INSTALLATION_STATE_COOKIE,
      state,
      cookieOptions,
    );

    // Store the current URL as the return_to URL so we can redirect back after installation
    const returnTo = request.headers.get("referer") || "/";
    response.cookies.set(
      GITHUB_INSTALLATION_RETURN_TO_COOKIE,
      returnTo,
      cookieOptions,
    );

    return response;
  } catch (error) {
    console.error("Error initiating GitHub App installation:", error);
    return NextResponse.json(
      { error: "Failed to initiate GitHub App installation" },
      { status: 500 },
    );
  }
}
