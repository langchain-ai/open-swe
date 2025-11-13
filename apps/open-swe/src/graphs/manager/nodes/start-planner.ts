import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import {
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
  LOCAL_MODE_HEADER,
} from "@openswe/shared/constants";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { PlannerGraphUpdate } from "@openswe/shared/open-swe/planner/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import {
  getInitialUserRequestDetails,
  getRecentUserRequest,
  getRecentUserRequestDetails,
} from "../../../utils/user-request.js";
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
  const initialRequestDetails = getInitialUserRequestDetails(state.messages);
  const followupRequestDetails = getRecentUserRequestDetails(state.messages, {
    config,
  });

  const localMode = isLocalMode(config);
  const defaultHeaders: Record<string, string> = localMode
    ? { [LOCAL_MODE_HEADER]: "true" }
    : {};

  try {
    const langGraphClient = createLangGraphClient({
      defaultHeaders,
    });

    const aggregatedFeatureIds = Array.from(
      new Set(
        [
          ...(state.activeFeatureIds ?? []),
          ...initialRequestDetails.featureIds,
          ...followupRequestDetails.featureIds,
        ]
          .map((featureId) => featureId.trim())
          .filter((featureId) => featureId.length > 0),
      ),
    );

    const runInput: PlannerGraphUpdate = {
      issueId: state.issueId,
      targetRepository: state.targetRepository,
      taskPlan: state.taskPlan,
      branchName: state.branchName ?? "",
      autoAcceptPlan: state.autoAcceptPlan,
      workspacePath: state.workspacePath,
      ...(followupMessage ? { messages: [followupMessage] } : {}),
      ...(aggregatedFeatureIds.length > 0
        ? { activeFeatureIds: aggregatedFeatureIds }
        : {}),
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
            ...(state.workspacePath
              ? { workspacePath: state.workspacePath }
              : {}),
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
      ...(aggregatedFeatureIds.length > 0
        ? { activeFeatureIds: aggregatedFeatureIds }
        : {}),
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
