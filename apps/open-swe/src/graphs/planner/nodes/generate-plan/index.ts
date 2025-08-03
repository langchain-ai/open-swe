import { v4 as uuidv4 } from "uuid";
import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createSessionPlanToolFields } from "../../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { formatUserRequestPrompt } from "../../../../utils/user-request.js";
import {
  formatFollowupMessagePrompt,
  isFollowupRequest,
} from "../../utils/followup.js";
import { stopSandbox } from "../../../../utils/sandbox.js";
import { z } from "zod";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { getScratchpad } from "../../utils/scratchpad-notes.js";
import { SCRATCHPAD_PROMPT, SYSTEM_PROMPT } from "./prompt.js";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";
import { filterMessagesWithoutContent } from "../../../../utils/message/content.js";
import { getModelManager } from "../../../../utils/llms/model-manager.js";
import { trackCachePerformance } from "../../../../utils/caching.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = isFollowupRequest(state.taskPlan, state.proposedPlan);
  const scratchpad = getScratchpad(state.messages)
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
    .replace("{USER_REQUEST_PROMPT}", formatUserRequestPrompt(state.messages))
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules))
    .replaceAll(
      "{SCRATCHPAD}",
      scratchpad.length
        ? SCRATCHPAD_PROMPT.replace("{SCRATCHPAD}", scratchpad)
        : "",
    );
}

export async function generatePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, LLMTask.PLANNER);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(config, LLMTask.PLANNER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.PLANNER,
  );
  const sessionPlanTool = createSessionPlanToolFields();
  const modelWithTools = model.bindTools([sessionPlanTool], {
    tool_choice: sessionPlanTool.name,
    ...(modelSupportsParallelToolCallsParam
      ? {
          parallel_tool_calls: false,
        }
      : {}),
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

  const inputMessages = filterMessagesWithoutContent([
    ...state.messages,
    ...(optionalToolMessage ? [optionalToolMessage] : []),
  ]);
  if (!inputMessages.length) {
    throw new Error("No messages to process.");
  }

  const response = await modelWithTools
    .withConfig({ tags: ["nostream"] })
    .invoke([
      {
        role: "system",
        content: formatSystemPrompt(state),
      },
      ...inputMessages,
    ]);

  // Filter out empty plans
  response.tool_calls = response.tool_calls?.map((tc) => {
    if (tc.id === sessionPlanTool.name) {
      return {
        ...tc,
        args: {
          ...tc.args,
          plan: (tc.args as z.infer<typeof sessionPlanTool.schema>).plan.filter(
            (p) => p.length > 0,
          ),
        },
      };
    }
    return tc;
  });

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("Failed to generate plan");
  }

  let newSessionId: string | undefined;
  if (state.sandboxSessionId && !isLocalMode(config)) {
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
    tokenData: trackCachePerformance(response, modelName),
  };
}
