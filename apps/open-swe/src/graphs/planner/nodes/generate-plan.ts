import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createSessionPlanToolFields } from "../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { loadModel, Task } from "../../../utils/load-model.js";
import { PlannerGraphState, PlannerGraphUpdate } from "@open-swe/shared/open-swe/planner/types";
import { getUserRequest } from "../../../utils/user-request.js";
import {
  formatFollowupMessagePrompt,
  isFollowupRequest,
} from "../utils/followup.js";
import { stopSandbox } from "../../../utils/sandbox.js";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.
{FOLLOWUP_MESSAGE_PROMPT}

In this step, you are expected to generate a high-level plan to address the user's request. The plan should be a list of actions to take, in order, to address the user's request. You should not include any code in the plan, only a list of actions to take.

You MUST adhere to the following criteria when generating the plan:
- You have already gathered context from the repository the user has requested you take actions on, and are now ready to generate a plan based on it.
  - This context is provided in the conversation history below.
- Your plan should be high-level in nature, but should still be specific enough to be actionable.
- Ensure your plan is as concise as possible. Omit any unnecessary details or steps the user did not request, or are not required to complete the task.
  - Your goal is to complete the task outlined by the user in the least number of steps possible.
- Do not pack multiple complex tasks into a single plan item. Each high level task you'll need to complete should have its own plan item.
- When you are ready to generate the plan, ensure you call the 'session_plan' tool. You are REQUIRED to call this tool.
- Your plan should be as simple as possible, while still containing all the tasks required to complete the user's request.
  - If the user did not explicitly request you write tests, do not include a task to write tests.
  - If the user did not explicitly request you write documentation, do not include a task to do so.
  - You should aim to complete the user's request in the least number of steps possible.


The user's request is as follows. Ensure you generate your plan in accordance with the user's request.
{USER_REQUEST}
`;

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = isFollowupRequest(state.taskPlan, state.proposedPlan);
  const userRequest = getUserRequest(state.messages);

  return systemPrompt
    .replace(
      "{FOLLOWUP_MESSAGE_PROMPT}",
      isFollowup
        ? formatFollowupMessagePrompt(state.taskPlan, state.proposedPlan)
        : "",
    )
    .replace("{USER_REQUEST}", userRequest);
}

export async function generatePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.PLANNER);
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
      ...state.messages,
      ...(optionalToolMessage ? [optionalToolMessage] : []),
    ]);

  if (!response.tool_calls?.length) {
    throw new Error("Failed to generate plan");
  }

  let newSessionId: string | undefined;
  if (state.sandboxSessionId) {
    // Stop before returning, as the next step will be to interrupt the graph.
    newSessionId = await stopSandbox(state.sandboxSessionId);
  }

  return {
    messages: [response],
    proposedPlan: response.tool_calls[0].args.plan,
    ...(newSessionId && { sandboxSessionId: newSessionId }),
  };
}
