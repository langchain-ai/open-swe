import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
} from "@open-swe/shared/constants";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { decryptGitHubToken } from "@open-swe/shared/crypto";

export function getGitHubTokensFromConfig(config: GraphConfig): {
  githubAccessToken: string;
  githubInstallationToken: string;
} {
  if (!config.configurable) {
    throw new Error("No configurable object found in graph config.");
  }
  const encryptedGitHubToken = config.configurable[GITHUB_TOKEN_COOKIE];
  const encryptedInstallationToken =
    config.configurable[GITHUB_INSTALLATION_TOKEN_COOKIE];
  if (!encryptedGitHubToken || !encryptedInstallationToken) {
    throw new Error(
      "Missing required x-github-access-token or x-github-installation-token in configuration.",
    );
  }

  // Get the encryption key from environment variables
  const encryptionKey = process.env.GITHUB_TOKEN_ENCRYPTION_KEY;
  if (!encryptionKey) {
    throw new Error(
      "Missing GITHUB_TOKEN_ENCRYPTION_KEY environment variable.",
    );
  }

  // Decrypt the GitHub token
  const githubAccessToken = decryptGitHubToken(
    encryptedGitHubToken,
    encryptionKey,
  );
  const githubInstallationToken = decryptGitHubToken(
    encryptedInstallationToken,
    encryptionKey,
  );

  return { githubAccessToken, githubInstallationToken };
}
