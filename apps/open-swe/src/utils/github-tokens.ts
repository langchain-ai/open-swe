import { GraphConfig } from "@open-swe/shared/open-swe/types";

export function getGitHubTokensFromConfig(config: GraphConfig): {
  githubToken: string;
  githubAccessToken: string;
} {
  if (!config.configurable) {
    throw new Error("No configurable object found in graph config.");
  }
  const githubToken = config.configurable["x-github-installation-token"];
  const githubAccessToken = config.configurable["x-github-access-token"];
  if (!githubToken) {
    throw new Error(
      "Missing required x-github-installation-token in configuration.",
    );
  }
  if (!githubAccessToken) {
    throw new Error("Missing required x-github-access-token in configuration.");
  }
  return { githubToken, githubAccessToken };
}
