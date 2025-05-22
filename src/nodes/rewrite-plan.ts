import { GraphState, GraphConfig, GraphUpdate } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import { sessionPlanTool } from "../tools/index.js";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

In this step, you are expected to rewrite a high-level plan to address the user's initial request. In a previous step you generated a plan, however the user has requested some changes:
## User Request
{USER_REQUEST}

Here is the previous plan:
## Previous Plan
{PREVIOUS_PLAN}

The plan must be a list of actions to take, in order, to address the user's request. You should not include any code in the plan, only a list of actions to take.

You MUST adhere to the following criteria when generating the plan:
- You do not have access to the codebase yet, so you cannot inspect it or make assumptions about it.
- Your plan should be high-level in nature, but should still be specific enough to be actionable.
- Make as few changes as possible to the previous plan, while still addressing the user's request.
- When you are ready to generate the plan, ensure you call the 'session_plan' tool.
- Ensure you generate the full plan in this tool call, not just the changes.
`;

const formatSysPrompt = (userRequest: string, previousPlan: string) => {
  return systemPrompt
    .replace("{USER_REQUEST}", userRequest)
    .replace("{PREVIOUS_PLAN}", previousPlan);
};

export async function rewritePlan(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  if (!state.planChangeRequest) {
    throw new Error("No plan change request found.");
  }

  const model = await loadModel(config, Task.PLANNER);
  const modelWithTools = model.bindTools([sessionPlanTool], {
    // The model should always call the tool when rewriting the plan.
    tool_choice: sessionPlanTool.name,
  });

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatSysPrompt(
        state.planChangeRequest,
        "  - " + state.plan.join("\n  - "),
      ),
    },
    ...state.messages,
  ]);

  if (response.tool_calls?.length) {
    return {
      proposedPlan: response.tool_calls[0].args.plan,
      plan: [],
    };
  }

  throw new Error("Failed to rewrite plan.");
}
