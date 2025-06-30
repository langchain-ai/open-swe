import { Octokit } from "@octokit/rest";
import { Endpoints } from "@octokit/types";

export type GithubUser = Endpoints["GET /user"]["response"]["data"];

/**
 * Verifies a GitHub user access token.
 * @param accessToken The GitHub user access token.
 * @returns A promise that resolves with the user object if valid, otherwise undefined.
 */
export async function verifyGithubUser(
  accessToken: string,
): Promise<GithubUser | undefined> {
  if (!accessToken) {
    return undefined;
  }

  try {
    const octokit = new Octokit({ auth: accessToken });
    const { data: user } = await octokit.users.getAuthenticated();
    if (!user || !user.login) {
      return undefined;
    }
    return user;
  } catch {
    return undefined;
  }
}

/**
 * Verifies a GitHub user ID using the app installation token. Checks that the provided
 * user ID is valid, and the provided login matches the user's login.
 * @param installationToken The GitHub installation token.
 * @param userId The GitHub user ID.
 * @param userLogin The GitHub user login.
 * @returns A promise that resolves with the user object if valid, otherwise undefined.
 */
export async function verifyGithubUserId(
  installationToken: string,
  userId: number,
  userLogin: string,
): Promise<GithubUser | undefined> {
  try {
    const octokit = new Octokit({ auth: installationToken });
    const { data: user } = await octokit.users.getById({ account_id: userId });
    if (!user || !user.login) {
      return undefined;
    }
    if (user.login !== userLogin) {
      return undefined;
    }
    return user;
  } catch {
    return undefined;
  }
}
