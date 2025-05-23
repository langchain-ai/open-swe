import { isHumanMessage } from "@langchain/core/messages";
import { sessionPlanTool } from "../../../tools/index.js";
import { GraphConfig } from "../../../types.js";
import { loadModel, Task } from "../../../utils/load-model.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { pauseSandbox } from "../../../utils/sandbox.js";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

In this step, you are expected to generate a high-level plan to address the user's request. The plan should be a list of actions to take, in order, to address the user's request. You should not include any code in the plan, only a list of actions to take.

You MUST adhere to the following criteria when generating the plan:
- You have already gathered context from the repository the user has requested you take actions on. This context is provided in the conversation history below.
- Your plan should be high-level in nature, but should still be specific enough to be actionable.
- Ensure your plan is as concise as possible. Omit any unnecessary details or steps. Your goal is to complete the task in the least number of steps possible.
- Do not pack multiple complex tasks into a single plan item. Each high level task you'll need to complete should have its own plan item.
- When you are ready to generate the plan, ensure you call the 'session_plan' tool. You are REQUIRED to call this tool.
- The first user message in this conversation contains the user's request.
`;

export async function generatePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.PLANNER);
  const modelWithTools = model.bindTools([sessionPlanTool], {
    tool_choice: sessionPlanTool.name,
  });

  const firstUserMessage = state.messages.find(isHumanMessage);

  const response = await modelWithTools
    .bind({ tags: ["langsmith:nostream"] })
    .invoke([
      {
        role: "system",
        content: systemPrompt,
      },
      ...(firstUserMessage ? [firstUserMessage] : []),
      ...state.plannerMessages,
    ]);

  if (!response.tool_calls?.length) {
    throw new Error("Failed to generate plan");
  }

  let newSessionId: string | undefined;
  if (state.sandboxSessionId) {
    // Pause before returning, as the next step will be to interrupt the graph.
    newSessionId = await pauseSandbox(state.sandboxSessionId);
  }

  return {
    proposedPlan: response.tool_calls[0].args.plan,
    plan: [],
    ...(newSessionId && { sandboxSessionId: newSessionId }),
  };
}
