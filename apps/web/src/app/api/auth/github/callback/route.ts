import {
  GITHUB_AUTH_STATE_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITHUB_TOKEN_COOKIE,
} from "@openswe/shared/constants";
import { getInstallationCookieOptions, createSession } from "@/lib/auth";
import { verifyGithubUser } from "@openswe/shared/github/verify-user";
import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const error = searchParams.get("error");
    const installationId = searchParams.get("installation_id");

    // Handle GitHub App errors
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
    const storedState = request.cookies.get(GITHUB_AUTH_STATE_COOKIE)?.value;

    if (storedState && state !== storedState) {
      return NextResponse.redirect(
        new URL("/?error=invalid_state", request.url),
      );
    }

    const clientId = process.env.NEXT_PUBLIC_GITHUB_APP_CLIENT_ID;
    const clientSecret = process.env.GITHUB_APP_CLIENT_SECRET;
    const redirectUri = process.env.GITHUB_APP_REDIRECT_URI;

    if (!clientId || !clientSecret || !redirectUri) {
      return NextResponse.redirect(
        new URL("/?error=configuration_missing", request.url),
      );
    }

    // Exchange authorization code for access token
    const tokenResponse = await fetch(
      "https://github.com/login/oauth/access_token",
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          client_id: clientId,
          client_secret: clientSecret,
          code: code,
          redirect_uri: redirectUri,
        }),
      },
    );

    if (!tokenResponse.ok) {
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
    const user = await verifyGithubUser(tokenData.access_token);
    if (!user) {
      return NextResponse.redirect(
        new URL("/?error=invalid_token", request.url),
      );
    }

    const response = NextResponse.redirect(new URL("/chat", request.url));

    response.cookies.set(GITHUB_AUTH_STATE_COOKIE, "", {
      expires: new Date(0),
      path: "/",
    });

    createSession(
      {
        accessToken: tokenData.access_token,
        tokenType: tokenData.token_type || "bearer",
        installationId: installationId ?? undefined,
        user: {
          login: user.login,
          avatar_url: user.avatar_url,
          html_url: user.html_url,
          name: user.name,
          email: user.email,
        },
      },
      response,
    );

    response.cookies.set(GITHUB_TOKEN_COOKIE, tokenData.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 30,
      path: "/",
    });

    if (installationId) {
      response.cookies.set(
        GITHUB_INSTALLATION_ID_COOKIE,
        installationId,
        getInstallationCookieOptions(),
      );
    }

    return response;
  } catch {
    return NextResponse.redirect(
      new URL("/?error=callback_failed", request.url),
    );
  }
}
