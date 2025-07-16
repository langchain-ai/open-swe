import { GITHUB_AUTH_STATE_COOKIE } from "@open-swe/shared/constants";
import { NextRequest, NextResponse } from "next/server";

export async function GET(_request: NextRequest) {
  try {
    const clientId = process.env.NEXT_PUBLIC_GITHUB_APP_CLIENT_ID;
    const redirectUri = process.env.GITHUB_APP_REDIRECT_URI;

    if (!clientId || !redirectUri) {
      return NextResponse.json(
        { error: "GitHub App configuration missing" },
        { status: 500 },
      );
    }

    // Generate a random state parameter for security
    const state = crypto.randomUUID();

    // Build the GitHub App authorization URL
    const authUrl = new URL("https://github.com/login/oauth/authorize");
    authUrl.searchParams.set("client_id", clientId);
    authUrl.searchParams.set("redirect_uri", redirectUri);
    authUrl.searchParams.set("allow_signup", "true");
    authUrl.searchParams.set("state", state);

    // Create response with redirect and store state in a cookie
    const response = NextResponse.redirect(authUrl.toString());

    response.cookies.set(GITHUB_AUTH_STATE_COOKIE, state, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: 60 * 10, // 10 minutes
      path: "/",
    });

    return response;
  } catch (error) {
    console.error("GitHub App login error:", error);
    return NextResponse.json(
      { error: "Failed to initiate GitHub App authentication flow" },
      { status: 500 },
    );
  }
}
