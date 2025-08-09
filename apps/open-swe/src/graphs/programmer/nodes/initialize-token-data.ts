import { GraphConfig, GraphUpdate } from "@open-swe/shared/open-swe/types";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";
import { getIssue } from "../../../utils/github/api.js";
import { extractTokenDataFromIssueContent } from "../../../utils/github/issue-task.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "InitializeTokenData");

export async function initializeTokenData(
  state: GraphUpdate,
  config: GraphConfig,
): Promise<GraphUpdate> {
  // Skip token data initialization in local mode
  if (isLocalMode(config)) {
    return {};
  }

  // Skip if no GitHub issue ID or target repository
  if (!state.githubIssueId || !state.targetRepository) {
    return {};
  }

  const { githubInstallationToken } = getGitHubTokensFromConfig(config);

  try {
    // Fetch the issue to get the latest token data
    const issue = await getIssue({
      owner: state.targetRepository.owner,
      repo: state.targetRepository.repo,
      issueNumber: state.githubIssueId,
      githubInstallationToken,
    });

    if (!issue || !issue.body) {
      return {};
    }

    // Extract token data from the issue body
    const tokenData = extractTokenDataFromIssueContent(issue.body);

    if (tokenData) {
      // Return token data with replace flag to reset the state
      return {
        tokenData: { data: tokenData, replaceMode: true },
      };
    }
  } catch (error) {
    // Log error but don't fail the graph execution
    logger.error("Failed to initialize token data from issue", {
      ...(error instanceof Error && {
        name: error.name,
        message: error.message,
        stack: error.stack,
      }),
    });
  }

  return {};
}
