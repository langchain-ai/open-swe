/**
 * GitLab Authentication Utilities
 *
 * Handles OAuth2 authentication flow for GitLab
 */

export interface GitLabOAuthTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  refresh_token: string;
  created_at: number;
}

export interface GitLabUserInfo {
  id: number;
  username: string;
  name: string;
  email: string;
  avatar_url: string;
}

/**
 * Exchanges an OAuth authorization code for an access token
 */
export async function getGitLabAccessToken(
  code: string,
  clientId: string,
  clientSecret: string,
  redirectUri: string,
  baseUrl: string = "https://gitlab.com",
): Promise<GitLabOAuthTokenResponse> {
  const response = await fetch(`${baseUrl}/oauth/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      code,
      grant_type: "authorization_code",
      redirect_uri: redirectUri,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(
      `Failed to get GitLab access token: ${JSON.stringify(errorData)}`,
    );
  }

  const data = await response.json();
  return data as GitLabOAuthTokenResponse;
}

/**
 * Refreshes an expired access token using a refresh token
 */
export async function refreshGitLabAccessToken(
  refreshToken: string,
  clientId: string,
  clientSecret: string,
  baseUrl: string = "https://gitlab.com",
): Promise<GitLabOAuthTokenResponse> {
  const response = await fetch(`${baseUrl}/oauth/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      refresh_token: refreshToken,
      grant_type: "refresh_token",
    }),
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(
      `Failed to refresh GitLab access token: ${JSON.stringify(errorData)}`,
    );
  }

  const data = await response.json();
  return data as GitLabOAuthTokenResponse;
}

/**
 * Gets the current user's information using an access token
 */
export async function getGitLabUser(
  accessToken: string,
  baseUrl: string = "https://gitlab.com",
): Promise<GitLabUserInfo> {
  const response = await fetch(`${baseUrl}/api/v4/user`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(
      `Failed to get GitLab user info: ${JSON.stringify(errorData)}`,
    );
  }

  const data = await response.json();
  return data as GitLabUserInfo;
}

/**
 * Verifies a GitLab access token is valid
 */
export async function verifyGitLabToken(
  accessToken: string,
  baseUrl: string = "https://gitlab.com",
): Promise<boolean> {
  try {
    await getGitLabUser(accessToken, baseUrl);
    return true;
  } catch {
    return false;
  }
}
