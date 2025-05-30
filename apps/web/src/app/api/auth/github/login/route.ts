import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  try {
    const clientId = process.env.GITHUB_CLIENT_ID;
    const redirectUri = process.env.GITHUB_REDIRECT_URI;

    if (!clientId || !redirectUri) {
      return NextResponse.json(
        { error: "GitHub OAuth configuration missing" },
        { status: 500 },
      );
    }

    // Generate a random state parameter for security
    const state = crypto.randomUUID();

    // Define the required scopes for the GitHub app
    const scopes = ["repo", "read:user"].join(" ");

    // Build the GitHub OAuth authorization URL
    const authUrl = new URL("https://github.com/login/oauth/authorize");
    authUrl.searchParams.set("client_id", clientId);
    authUrl.searchParams.set("redirect_uri", redirectUri);
    authUrl.searchParams.set("scope", scopes);
    authUrl.searchParams.set("state", state);

    return NextResponse.redirect(authUrl.toString());
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to initiate OAuth flow" },
      { status: 500 },
    );
  }
}
