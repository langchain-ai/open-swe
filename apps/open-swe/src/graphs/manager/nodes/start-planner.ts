import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@open-swe/shared/open-swe/manager/types";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import {
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
  LOCAL_MODE_HEADER,
  GITHUB_INSTALLATION_ID,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_PAT,
} from "@open-swe/shared/constants";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { getBranchName } from "../../../utils/github/git.js";
import { PlannerGraphUpdate } from "@open-swe/shared/open-swe/planner/types";
import { getDefaultHeaders } from "../../../utils/default-headers.js";
import { getCustomConfigurableFields } from "../../../utils/config.js";
import { getRecentUserRequest } from "../../../utils/user-request.js";
import { StreamMode } from "@langchain/langgraph-sdk";
import { regenerateInstallationToken } from "../../../utils/github/regenerate-token.js";

const logger = createLogger(LogLevel.INFO, "StartPlanner");

/**
 * Start planner node.
 * This node will kickoff a new planner session using the LangGraph SDK.
 * In local mode, creates a planner session with local mode headers.
 */
export async function startPlanner(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<ManagerGraphUpdate> {
  const plannerThreadId = state.plannerSession?.threadId ?? uuidv4();
  const followupMessage = getRecentUserRequest(state.messages, {
    returnFullMessage: true,
    config,
  });

  const localMode = isLocalMode(config);
  const defaultHeaders = localMode
    ? { [LOCAL_MODE_HEADER]: "true" }
    : getDefaultHeaders(config);

  // Only regenerate if its not running in local mode, and the GITHUB_PAT is not in the headers
  // If the GITHUB_PAT is in the headers, then it means we're running an eval and this does not need to be regenerated
  if (!localMode && !(GITHUB_PAT in defaultHeaders)) {
    logger.info("Regenerating installation token before starting planner run.");
    defaultHeaders[GITHUB_INSTALLATION_TOKEN_COOKIE] =
      await regenerateInstallationToken(defaultHeaders[GITHUB_INSTALLATION_ID]);
    logger.info("Regenerated installation token before starting planner run.");
  }

  try {
    const langGraphClient = createLangGraphClient({
      defaultHeaders,
    });

    const runInput: PlannerGraphUpdate = {
      // github issue ID & target repo so the planning agent can fetch the user's request, and clone the repo.
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
      // Include the existing task plan, so the agent can use it as context when generating followup tasks.
      taskPlan: state.taskPlan,
      branchName: state.branchName ?? getBranchName(config),
      autoAcceptPlan: state.autoAcceptPlan,
      ...(followupMessage || localMode ? { messages: [followupMessage] } : {}),
    };

    const run = await langGraphClient.runs.create(
      plannerThreadId,
      PLANNER_GRAPH_ID,
      {
        input: runInput,
        config: {
          recursion_limit: 400,
          configurable: {
            ...getCustomConfigurableFields(config),
            ...(isLocalMode(config) && {
              [LOCAL_MODE_HEADER]: "true",
            }),
          },
        },
        ifNotExists: "create",
        streamResumable: true,
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      },
    );

    return {
      plannerSession: {
        threadId: plannerThreadId,
        runId: run.run_id,
      },
    };
  } catch (error) {
    logger.error("Failed to start planner", {
      ...(error instanceof Error
        ? {
            name: error.name,
            message: error.message,
            stack: error.stack,
          }
        : {
            error,
          }),
    });
    throw error;
  }
}
