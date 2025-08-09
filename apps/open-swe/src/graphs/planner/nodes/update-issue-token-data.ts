import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { addTokenDataToIssue } from "../../../utils/github/issue-task.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "UpdateIssueTokenData");

export async function updateIssueTokenData(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  // Skip token data update in local mode
  if (isLocalMode(config)) {
    return {};
  }

  // Skip if no token data to update
  if (!state.tokenData || state.tokenData.length === 0) {
    return {};
  }

  // Skip if no GitHub issue ID or target repository
  if (!state.githubIssueId || !state.targetRepository) {
    return {};
  }

  try {
    // Update the GitHub issue with the current token data
    await addTokenDataToIssue(
      {
        githubIssueId: state.githubIssueId,
        targetRepository: state.targetRepository,
      },
      config,
      state.tokenData,
    );
  } catch (error) {
    // Log error but don't fail the graph execution
    logger.error("Failed to update token data in issue", {
      ...(error instanceof Error && {
        name: error.name,
        message: error.message,
        stack: error.stack,
      }),
    });
  }

  return {};
}
