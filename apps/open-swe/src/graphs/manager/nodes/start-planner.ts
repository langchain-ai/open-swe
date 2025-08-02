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
} from "@open-swe/shared/constants";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { getBranchName } from "../../../utils/github/git.js";
import { PlannerGraphUpdate } from "@open-swe/shared/open-swe/planner/types";
import { getDefaultHeaders } from "../../../utils/default-headers.js";
import { getCustomConfigurableFields } from "../../../utils/config.js";
import { getRecentUserRequest } from "../../../utils/user-request.js";
import { StreamMode } from "@langchain/langgraph-sdk";

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

  try {
    const langGraphClient = createLangGraphClient({
      defaultHeaders: localMode
        ? {
            [LOCAL_MODE_HEADER]: "true",
          }
        : getDefaultHeaders(config),
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
        metadata: {
          source: "manager:start_planner",
          owner: state.targetRepository?.owner,
          repo: state.targetRepository?.repo,
        },
        config: {
          recursion_limit: 400,
          configurable: getCustomConfigurableFields(config),
        },
        ifNotExists: "create",
        multitaskStrategy: "enqueue",
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
