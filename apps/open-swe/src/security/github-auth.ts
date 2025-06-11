import { Octokit } from "@octokit/rest";
import { Endpoints } from "@octokit/types";
import { createLogger, LogLevel } from "../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "GithubAuth");

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
      logger.error(
        "GitHub token is invalid or user information could not be retrieved.",
      );
      return undefined;
    }

    return user;
  } catch (error) {
    logger.error("An error occurred during GitHub user verification:", error);
    return undefined;
  }
}
