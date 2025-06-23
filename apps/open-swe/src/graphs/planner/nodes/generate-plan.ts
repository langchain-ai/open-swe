import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createSessionPlanToolFields } from "../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { loadModel, Task } from "../../../utils/load-model.js";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { getUserRequest } from "../../../utils/user-request.js";
import {
  formatFollowupMessagePrompt,
  isFollowupRequest,
} from "../utils/followup.js";
import { stopSandbox } from "../../../utils/sandbox.js";
import { filterHiddenMessages } from "../../../utils/message/filter-hidden.js";
import { z } from "zod";
import { formatCustomRulesPrompt } from "../../../utils/custom-rules.js";

const systemPrompt = `You are a terminal-based agentic coding assistant built by LangChain, designed to enable natural language interaction with local codebases through wrapped LLM models.

<context>{FOLLOWUP_MESSAGE_PROMPT}
You have already gathered comprehensive context from the repository through the conversation history below. All previous messages will be deleted after this planning step, so your plan must be self-contained and actionable without referring back to this context.
</context>

<task>
Generate a high-level execution plan to address the user's request. Your plan will guide the implementation phase, so each action must be specific and actionable.

<user_request>
{USER_REQUEST}
</user_request>
</task>

<instructions>
Create your plan following these guidelines:

1. **Structure each action item to include:**
   - The specific task to accomplish
   - Key technical details needed for execution
   - File paths, function names, or other concrete references from the context you've gathered

2. **Write actionable items that:**
   - Focus on implementation steps, not information gathering
   - Can be executed independently without additional context discovery
   - Build upon each other in logical sequence
   - Are not open ended, and require additional context to execute

3. **Optimize for efficiency by:**
   - Completing the request in the minimum number of steps
   - Reusing existing code and patterns wherever possible
   - Writing reusable components when code will be used multiple times

4. **Include only what's requested:**
   - Add testing steps only if the user explicitly requested tests
   - Add documentation steps only if the user explicitly requested documentation
   - Focus solely on fulfilling the stated requirements
</instructions>

<output_format>
When ready, call the 'session_plan' tool with your plan. Each plan item should be a complete, self-contained action that can be executed without referring back to this conversation.

Structure your plan items as clear directives, for example:
- "Implement function X in file Y that performs Z using the existing pattern from file A"
- "Modify the authentication middleware in /src/auth.js to add rate limiting using the Express rate-limit package"
</output_format>

{CUSTOM_RULES}

Remember: Your goal is to create a focused, executable plan that efficiently accomplishes the user's request using the context you've already gathered.`;

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = isFollowupRequest(state.taskPlan, state.proposedPlan);
  const userRequest = getUserRequest(state.messages);

  return systemPrompt
    .replace(
      "{FOLLOWUP_MESSAGE_PROMPT}",
      isFollowup
        ? "\n" +
            formatFollowupMessagePrompt(state.taskPlan, state.proposedPlan) +
            "\n\n"
        : "",
    )
    .replace("{USER_REQUEST}", userRequest)
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules));
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
      ...filterHiddenMessages(state.messages),
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

  const proposedPlanArgs = response.tool_calls[0].args as z.infer<
    typeof sessionPlanTool.schema
  >;

  return {
    messages: [response],
    proposedPlanTitle: proposedPlanArgs.title,
    proposedPlan: proposedPlanArgs.plan,
    ...(newSessionId && { sandboxSessionId: newSessionId }),
  };
}
