import { loadModel, Task } from "../../../../utils/load-model.js";
import { createShellTool } from "../../../../tools/index.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../../types.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { getUserRequest } from "../../../../utils/user-request.js";
import { isHumanMessage } from "@langchain/core/messages";
import { formatFollowupMessagePrompt } from "../../utils/followup-prompt.js";
import { getRepoAbsolutePath } from "../../../../utils/git.js";
import { SYSTEM_PROMPT } from "./prompt.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = state.internalMessages.filter(isHumanMessage).length > 1;
  return SYSTEM_PROMPT.replace(
    "{FOLLOWUP_MESSAGE_PROMPT}",
    isFollowup ? formatFollowupMessagePrompt(state.plan) : "",
  )
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    )
    .replaceAll(
      "{CURRENT_WORKING_DIRECTORY}",
      getRepoAbsolutePath(state.targetRepository),
    );
}

export async function generateAction(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [createShellTool(state)];
  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    parallel_tool_calls: false,
  });

  const userRequest = getUserRequest(state.internalMessages, {
    returnFullMessage: true,
  });

  const response = await modelWithTools
    .withConfig({ tags: ["nostream"] })
    .invoke([
      {
        role: "system",
        content: formatSystemPrompt(state),
      },
      userRequest,
      ...state.plannerMessages,
    ]);

  logger.info("Generated planning message", {
    ...(getMessageContentString(response.content) && {
      content: getMessageContentString(response.content),
    }),
    ...(response.tool_calls?.[0] && {
      name: response.tool_calls?.[0].name,
      args: response.tool_calls?.[0].args,
    }),
  });

  return {
    messages: [response],
    plannerMessages: [response],
  };
}
