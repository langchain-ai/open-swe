/**
 * Provider Factory
 *
 * Factory function to create git provider instances based on configuration
 */

import type { GitProvider, ProviderConfig, ProviderType } from "./types.js";
import { GitHubProvider } from "./github/github-provider.js";
import { GitLabProvider } from "./gitlab/gitlab-provider.js";

/**
 * Creates a git provider instance based on the provided configuration
 *
 * @param config Provider configuration
 * @returns Configured git provider instance
 * @throws Error if provider type is not supported
 */
export function createGitProvider(config: ProviderConfig): GitProvider {
  switch (config.type) {
    case "github":
      return new GitHubProvider(config.token);

    case "gitlab":
      return new GitLabProvider(config.token, config.baseUrl);

    default:
      throw new Error(`Unsupported provider type: ${(config as any).type}`);
  }
}

/**
 * Creates a git provider instance from a simple type and token
 *
 * @param type Provider type ('github' or 'gitlab')
 * @param token Authentication token
 * @param baseUrl Optional base URL (for self-hosted GitLab)
 * @returns Configured git provider instance
 */
export function createGitProviderSimple(
  type: ProviderType,
  token: string,
  baseUrl?: string
): GitProvider {
  return createGitProvider({
    type,
    token,
    baseUrl,
  });
}

/**
 * Validates provider configuration
 *
 * @param config Provider configuration to validate
 * @returns True if configuration is valid
 * @throws Error if configuration is invalid
 */
export function validateProviderConfig(config: ProviderConfig): boolean {
  if (!config.type) {
    throw new Error("Provider type is required");
  }

  if (!config.token) {
    throw new Error("Provider token is required");
  }

  if (config.type === "gitlab" && config.baseUrl) {
    try {
      new URL(config.baseUrl);
    } catch (error) {
      throw new Error("Invalid GitLab base URL");
    }
  }

  return true;
}
