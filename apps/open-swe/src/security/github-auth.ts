import { Octokit } from "@octokit/rest";
import { Endpoints } from "@octokit/types";

export type GithubUser = Endpoints["GET /user"]["response"]["data"];

/**
 * Verifies a GitHub user access token and checks for membership in the 'langchain-ai' organization.
 *
 * @param accessToken The GitHub user access token.
 * @returns A promise that resolves with the user object if valid and a member, otherwise undefined.
 */
export async function verifyGithubUser(
  accessToken: string
): Promise<GithubUser | undefined> {
  if (!accessToken) {
    return undefined;
  }

  try {
    const octokit = new Octokit({ auth: accessToken });

    // 1. Fetch user information to validate the token
    const { data: user } = await octokit.users.getAuthenticated();

    if (!user || !user.login) {
      console.error(
        "GitHub token is invalid or user information could not be retrieved."
      );
      return undefined;
    }

    const username = user.login;

    // 2. List organizations for the user
    const { data: orgs } = await octokit.orgs.listForUser({
      username,
    });

    // 3. Check for 'langchain-ai' organization membership
    const isMember = orgs.some((org) => org.login === "langchain-ai");

    if (!isMember) {
      console.log(
        `User ${username} is not a member of the 'langchain-ai' organization.`
      );
      return undefined;
    }

    return user;
  } catch (error) {
    console.error("An error occurred during GitHub user verification:", error);
    return undefined;
  }
}