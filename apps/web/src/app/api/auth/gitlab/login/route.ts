import { NextRequest, NextResponse } from "next/server";

const GITLAB_AUTH_STATE_COOKIE = "gitlab_auth_state";

export async function GET(_request: NextRequest) {
  try {
    const clientId = process.env.NEXT_PUBLIC_GITLAB_APPLICATION_ID;
    const redirectUri = process.env.GITLAB_REDIRECT_URI;
    const baseUrl = process.env.NEXT_PUBLIC_GITLAB_BASE_URL || "https://gitlab.com";

    if (!clientId || !redirectUri) {
      return NextResponse.json(
        { error: "GitLab OAuth configuration missing" },
        { status: 500 },
      );
    }

    // Generate a random state parameter for security
    const state = crypto.randomUUID();

    // Build the GitLab OAuth authorization URL
    const authUrl = new URL(`${baseUrl}/oauth/authorize`);
    authUrl.searchParams.set("client_id", clientId);
    authUrl.searchParams.set("redirect_uri", redirectUri);
    authUrl.searchParams.set("response_type", "code");
    authUrl.searchParams.set("state", state);
    authUrl.searchParams.set("scope", "api read_user read_repository write_repository");

    // Create response with redirect and store state in a cookie
    const response = NextResponse.redirect(authUrl.toString());

    response.cookies.set(GITLAB_AUTH_STATE_COOKIE, state, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: 60 * 10, // 10 minutes
      path: "/",
    });

    return response;
  } catch (error) {
    console.error("GitLab OAuth login error:", error);
    return NextResponse.json(
      { error: "Failed to initiate GitLab OAuth authentication flow" },
      { status: 500 },
    );
  }
}
