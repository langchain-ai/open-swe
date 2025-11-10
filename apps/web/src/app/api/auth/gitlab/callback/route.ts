import { GITLAB_TOKEN_COOKIE, GITLAB_BASE_URL } from "@openswe/shared/constants";
import { NextRequest, NextResponse } from "next/server";

const GITLAB_AUTH_STATE_COOKIE = "gitlab_auth_state";
const GITLAB_USER_ID_COOKIE = "gitlab_user_id";
const GITLAB_USER_LOGIN_COOKIE = "gitlab_user_login";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const error = searchParams.get("error");

    // Handle OAuth errors
    if (error) {
      return NextResponse.redirect(
        new URL(`/?error=${encodeURIComponent(error)}`, request.url),
      );
    }

    // Validate required parameters
    if (!code) {
      return NextResponse.redirect(
        new URL("/?error=missing_code_parameter", request.url),
      );
    }

    // Verify state parameter to prevent CSRF attacks
    const storedState = request.cookies.get(GITLAB_AUTH_STATE_COOKIE)?.value;

    if (storedState && state !== storedState) {
      return NextResponse.redirect(
        new URL("/?error=invalid_state", request.url),
      );
    }

    const clientId = process.env.NEXT_PUBLIC_GITLAB_APPLICATION_ID;
    const clientSecret = process.env.GITLAB_APPLICATION_SECRET;
    const redirectUri = process.env.GITLAB_REDIRECT_URI;
    const baseUrl = process.env.NEXT_PUBLIC_GITLAB_BASE_URL || "https://gitlab.com";

    if (!clientId || !clientSecret || !redirectUri) {
      return NextResponse.redirect(
        new URL("/?error=configuration_missing", request.url),
      );
    }

    // Exchange authorization code for access token
    const tokenResponse = await fetch(`${baseUrl}/oauth/token`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
        code: code,
        grant_type: "authorization_code",
        redirect_uri: redirectUri,
      }),
    });

    if (!tokenResponse.ok) {
      console.error("Token exchange failed:", await tokenResponse.text());
      return NextResponse.redirect(
        new URL("/?error=token_exchange_failed", request.url),
      );
    }

    const tokenData = await tokenResponse.json();

    if (tokenData.error) {
      return NextResponse.redirect(
        new URL(`/?error=${encodeURIComponent(tokenData.error)}`, request.url),
      );
    }

    // Fetch user information
    let userInfo;
    try {
      const userResponse = await fetch(`${baseUrl}/api/v4/user`, {
        headers: {
          Authorization: `Bearer ${tokenData.access_token}`,
        },
      });

      if (userResponse.ok) {
        userInfo = await userResponse.json();
      }
    } catch (err) {
      console.error("Failed to fetch user info:", err);
    }

    // Create the success response
    const response = NextResponse.redirect(new URL("/chat", request.url));

    // Clear the state cookie as it's no longer needed
    response.cookies.set(GITLAB_AUTH_STATE_COOKIE, "", {
      expires: new Date(0),
      path: "/",
    });

    // Set token cookies directly on the response
    response.cookies.set(GITLAB_TOKEN_COOKIE, tokenData.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: tokenData.expires_in || 60 * 60 * 24 * 30, // Use expires_in or default to 30 days
      path: "/",
    });

    // Set GitLab base URL cookie
    response.cookies.set(GITLAB_BASE_URL, baseUrl, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: "/",
    });

    // Store user information if available
    if (userInfo) {
      response.cookies.set(GITLAB_USER_ID_COOKIE, userInfo.id.toString(), {
        httpOnly: true,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        maxAge: 60 * 60 * 24 * 30, // 30 days
        path: "/",
      });

      response.cookies.set(GITLAB_USER_LOGIN_COOKIE, userInfo.username, {
        httpOnly: true,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        maxAge: 60 * 60 * 24 * 30, // 30 days
        path: "/",
      });
    }

    return response;
  } catch (error) {
    console.error("GitLab OAuth callback error:", error);
    return NextResponse.redirect(
      new URL("/?error=callback_failed", request.url),
    );
  }
}
