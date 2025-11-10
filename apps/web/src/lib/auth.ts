import { NextRequest, NextResponse } from "next/server";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_TOKEN_TYPE_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITLAB_TOKEN_COOKIE,
} from "@openswe/shared/constants";

export const GITHUB_INSTALLATION_STATE_COOKIE = "github_installation_state";
export const GITHUB_INSTALLATION_RETURN_TO_COOKIE = "installation_return_to";

export interface GitHubTokenData {
  access_token: string;
  token_type: string;
  installation_id?: string;
}

/**
 * Cookie options for GitHub token cookies
 */
function getCookieOptions(expires?: Date) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    maxAge: expires ? undefined : 60 * 60 * 24 * 30, // 30 days
    expires,
    path: "/",
  };
}

/**
 * Cookie options for GitHub installation ID cookie (non-HTTP-only for client access)
 */
export function getInstallationCookieOptions(expires?: Date) {
  return {
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    maxAge: expires ? undefined : 60 * 60 * 24 * 30, // 30 days
    expires,
    path: "/",
  };
}

/**
 * Stores GitHub OAuth token data in secure HTTP-only cookies
 *
 * @param tokenData The GitHub token data to store
 * @param response NextResponse to set cookies on
 */
export function storeGitHubToken(
  tokenData: GitHubTokenData,
  response: NextResponse,
): void {
  const cookieOptions = getCookieOptions();

  // Store token components in separate cookies for better security
  response.cookies.set(
    GITHUB_TOKEN_COOKIE,
    tokenData.access_token,
    cookieOptions,
  );
  response.cookies.set(
    GITHUB_TOKEN_TYPE_COOKIE,
    tokenData.token_type || "bearer",
    cookieOptions,
  );

  // Store installation ID if provided
  if (tokenData.installation_id) {
    response.cookies.set(
      GITHUB_INSTALLATION_ID_COOKIE,
      tokenData.installation_id,
      getInstallationCookieOptions(),
    );
  }
}

/**
 * Retrieves GitHub OAuth token data from cookies
 *
 * @param request NextRequest to get cookies from
 */
export function getGitHubToken(request: NextRequest): GitHubTokenData | null {
  try {
    const accessToken = request.cookies.get(GITHUB_TOKEN_COOKIE)?.value;
    const tokenType = request.cookies.get(GITHUB_TOKEN_TYPE_COOKIE)?.value;
    const installationId = request.cookies.get(
      GITHUB_INSTALLATION_ID_COOKIE,
    )?.value;

    if (!accessToken) {
      return null;
    }

    return {
      access_token: accessToken,
      token_type: tokenType || "bearer",
      installation_id: installationId,
    };
  } catch (error) {
    console.error("Error retrieving GitHub token:", error);
    return null;
  }
}

/**
 * Removes GitHub OAuth token data from cookies (logout)
 *
 * @param response NextResponse to set cookies on
 */
export function clearGitHubToken(response: NextResponse): void {
  response.cookies.delete(GITHUB_TOKEN_COOKIE);
  response.cookies.delete(GITHUB_TOKEN_TYPE_COOKIE);
  response.cookies.delete(GITHUB_INSTALLATION_ID_COOKIE);
}

/**
 * Retrieves GitLab OAuth token from cookies
 *
 * @param request NextRequest to get cookies from
 */
export function getGitLabToken(request: NextRequest): string | null {
  try {
    const accessToken = request.cookies.get(GITLAB_TOKEN_COOKIE)?.value;
    return accessToken || null;
  } catch (error) {
    console.error("Error retrieving GitLab token:", error);
    return null;
  }
}

/**
 * Checks if user has a valid GitHub or GitLab token
 *
 * @param request NextRequest to get cookies from
 */
export function isAuthenticated(request: NextRequest): boolean {
  const githubToken = getGitHubToken(request);
  const gitlabToken = getGitLabToken(request);

  return (
    (githubToken !== null && githubToken.access_token.length > 0) ||
    (gitlabToken !== null && gitlabToken.length > 0)
  );
}

/**
 * Gets the authenticated provider type
 *
 * @param request NextRequest to get cookies from
 */
export function getAuthenticatedProvider(request: NextRequest): "github" | "gitlab" | null {
  const githubToken = getGitHubToken(request);
  const gitlabToken = getGitLabToken(request);

  if (githubToken !== null && githubToken.access_token.length > 0) {
    return "github";
  }
  if (gitlabToken !== null && gitlabToken.length > 0) {
    return "gitlab";
  }
  return null;
}
