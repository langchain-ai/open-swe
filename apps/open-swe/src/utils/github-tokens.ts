import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_PAT,
} from "@open-swe/shared/constants";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { decryptSecret } from "@open-swe/shared/crypto";

export function getGitHubTokensFromConfig(config: GraphConfig): {
  githubAccessToken: string;
  githubInstallationToken: string;
} {
  if (!config.configurable) {
    throw new Error("No configurable object found in graph config.");
  }

  const encryptionKey = process.env.GITHUB_TOKEN_ENCRYPTION_KEY;
  if (!encryptionKey) {
    throw new Error(
      "Missing GITHUB_TOKEN_ENCRYPTION_KEY environment variable.",
    );
  }

  const encryptedGitHubPat = config.configurable[GITHUB_PAT];
  if (encryptedGitHubPat) {
    // check for PAT-only mode
    const githubPat = decryptGitHubToken(encryptedGitHubPat, encryptionKey);
    return {
      githubAccessToken: githubPat,
      githubInstallationToken: githubPat,
    };
  }

  const encryptedGitHubToken = config.configurable[GITHUB_TOKEN_COOKIE];
  const encryptedInstallationToken =
    config.configurable[GITHUB_INSTALLATION_TOKEN_COOKIE];
  if (!encryptedInstallationToken) {
    throw new Error(
      `Missing required ${GITHUB_INSTALLATION_TOKEN_COOKIE} in configuration.`,
    );
  }

  // Get the encryption key from environment variables
  const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
  if (!encryptionKey) {
    throw new Error("Missing SECRETS_ENCRYPTION_KEY environment variable.");
  }

  // Decrypt the GitHub token
  const githubAccessToken = encryptedGitHubToken
    ? decryptSecret(encryptedGitHubToken, encryptionKey)
    : "";
  const githubInstallationToken = decryptSecret(
    encryptedInstallationToken,
    encryptionKey,
  );

  return { githubAccessToken, githubInstallationToken };
}
