export type GithubUser = {
  login: string;
};

/**
 * GitHub user verification is no longer supported.
 * These functions now return undefined.
 */
export async function verifyGithubUser(
  _accessToken: string,
): Promise<GithubUser | undefined> {
  return undefined;
}

export async function verifyGithubUserId(
  _installationToken: string,
  _userId: number,
  _userLogin: string,
): Promise<GithubUser | undefined> {
  return undefined;
}
