import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";
import { GraphConfig } from "@open-swe/shared/open-swe/types";

export function getGitHubTokensFromConfig(config: GraphConfig): {
  githubAccessToken: string;
} {
  if (!config.configurable) {
    throw new Error("No configurable object found in graph config.");
  }
  const githubAccessToken = config.configurable[GITHUB_TOKEN_COOKIE];
  if (!githubAccessToken) {
    throw new Error("Missing required x-github-access-token in configuration.");
  }
  return { githubAccessToken };
}
