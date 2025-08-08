import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID,
} from "@open-swe/shared/constants";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { decryptSecret } from "@open-swe/shared/crypto";
import { getGitHubPatFromConfig } from "./github-pat.js";

export function getGitHubTokensFromConfig(config: GraphConfig): {
  githubAccessToken: string;
  githubInstallationToken: string;
  installationId: string;
} {
  if (!config.configurable) {
    throw new Error("No configurable object found in graph config.");
  }

  // Get the encryption key from environment variables
  const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
  if (!encryptionKey) {
    throw new Error("Missing SECRETS_ENCRYPTION_KEY environment variable.");
  }

  const isProd = process.env.NODE_ENV === "production";

  const githubPat = getGitHubPatFromConfig(config.configurable, encryptionKey);
  if (githubPat && !isProd) {
    // check for PAT-only mode
    return {
      githubAccessToken: githubPat,
      githubInstallationToken: githubPat,
      // installationId is not required in PAT-only mode
      installationId: config.configurable[GITHUB_INSTALLATION_ID] ?? "",
    };
  }

  const installationId = config.configurable[GITHUB_INSTALLATION_ID];
  if (!installationId) {
    throw new Error(
      `Missing required ${GITHUB_INSTALLATION_ID} in configuration.`,
    );
  }

  const encryptedGitHubToken = config.configurable[GITHUB_TOKEN_COOKIE];
  const encryptedInstallationToken =
    config.configurable[GITHUB_INSTALLATION_TOKEN_COOKIE];
  if (!encryptedInstallationToken) {
    throw new Error(
      `Missing required ${GITHUB_INSTALLATION_TOKEN_COOKIE} in configuration.`,
    );
  }

  // Decrypt the GitHub token
  const githubAccessToken = encryptedGitHubToken
    ? decryptSecret(encryptedGitHubToken, encryptionKey)
    : "";
  const githubInstallationToken = decryptSecret(
    encryptedInstallationToken,
    encryptionKey,
  );

  return { githubAccessToken, githubInstallationToken, installationId };
}
