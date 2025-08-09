import { GraphConfig, GraphState } from "@open-swe/shared/open-swe/types";
import { addTokenDataToIssue } from "../../../utils/github/issue-task.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { Command, END } from "@langchain/langgraph";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "UpdateIssueTokenData");

export async function updateIssueTokenData(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  // Update token data if available and not in local mode
  if (
    !isLocalMode(config) &&
    state.tokenData &&
    state.tokenData.length > 0 &&
    state.githubIssueId &&
    state.targetRepository
  ) {
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
  }

  // Route based on mode: END for local mode, open-pr for sandbox mode
  if (isLocalMode(config)) {
    return new Command({
      goto: END,
    });
  } else {
    return new Command({
      goto: "open-pr",
    });
  }
}
