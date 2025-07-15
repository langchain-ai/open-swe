import { v4 as uuidv4 } from "uuid";
import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createSessionPlanToolFields } from "../../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { loadModel, Task } from "../../../../utils/load-model.js";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { getUserRequest } from "../../../../utils/user-request.js";
import {
  formatFollowupMessagePrompt,
  isFollowupRequest,
} from "../../utils/followup.js";
import { stopSandbox } from "../../../../utils/sandbox.js";
import { filterHiddenMessages } from "../../../../utils/message/filter-hidden.js";
import { z } from "zod";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { getPlannerNotes } from "../../utils/get-notes.js";
import { PLANNER_NOTES_PROMPT, SYSTEM_PROMPT } from "./prompt.js";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = isFollowupRequest(state.taskPlan, state.proposedPlan);
  const userRequest = getUserRequest(state.messages);
  const plannerNotes = getPlannerNotes(state.messages)
    .map((n) => `- ${n}`)
    .join("\n");
  return SYSTEM_PROMPT.replace(
    "{FOLLOWUP_MESSAGE_PROMPT}",
    isFollowup
      ? "\n" +
          formatFollowupMessagePrompt(state.taskPlan, state.proposedPlan) +
          "\n\n"
      : "",
  )
    .replace("{USER_REQUEST}", userRequest)
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules))
    .replaceAll(
      "{PLANNER_NOTES}",
      plannerNotes.length
        ? PLANNER_NOTES_PROMPT.replace("{PLANNER_NOTES}", plannerNotes)
        : "",
    );
}

export async function generatePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.PROGRAMMER);
  const sessionPlanTool = createSessionPlanToolFields();
  const modelWithTools = model.bindTools([sessionPlanTool], {
    tool_choice: sessionPlanTool.name,
    parallel_tool_calls: false,
  });

  let optionalToolMessage: ToolMessage | undefined;
  const lastMessage = state.messages[state.messages.length - 1];
  if (isAIMessage(lastMessage) && lastMessage.tool_calls?.[0]) {
    const lastMessageToolCall = lastMessage.tool_calls?.[0];
    optionalToolMessage = new ToolMessage({
      id: uuidv4(),
      tool_call_id: lastMessageToolCall.id ?? "",
      name: lastMessageToolCall.name,
      content: "Tool call not executed. Max actions reached.",
    });
  }

  const response = await modelWithTools
    .withConfig({ tags: ["nostream"] })
    .invoke([
      {
        role: "system",
        content: formatSystemPrompt(state),
      },
      ...filterHiddenMessages(state.messages),
      ...(optionalToolMessage ? [optionalToolMessage] : []),
    ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("Failed to generate plan");
  }

  let newSessionId: string | undefined;
  if (state.sandboxSessionId) {
    // Stop before returning, as the next step will be to interrupt the graph.
    newSessionId = await stopSandbox(state.sandboxSessionId);
  }

  const proposedPlanArgs = toolCall.args as z.infer<
    typeof sessionPlanTool.schema
  >;

  const toolResponse = new ToolMessage({
    id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
    tool_call_id: toolCall.id ?? "",
    content: "Successfully saved plan.",
    name: sessionPlanTool.name,
  });

  return {
    messages: [response, toolResponse],
    proposedPlanTitle: proposedPlanArgs.title,
    proposedPlan: proposedPlanArgs.plan,
    ...(newSessionId && { sandboxSessionId: newSessionId }),
  };
}
