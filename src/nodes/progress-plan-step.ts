import { z } from "zod";
import { GraphConfig, GraphState, GraphUpdate, PlanItem } from "../types.js";
import { loadModel } from "../utils/load-model.js";
import { formatPlanPrompt } from "../utils/plan-prompt.js";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

In your workflow, you generate a plan, then act on said plan. It may take many actions to complete a single step, or a single action to complete the step.

Here is the plan:

{PLAN_PROMPT}

In this task, you will analyze the plan, the tasks you've completed, the tasks which are left, and the current task you just took an action on. In addition to this, you're also provided the full conversation history between you and the user. All of the messages in this conversation are from the previous steps/actions you've taken, and any user input.

Take all of this information, and determine whether or not you have completed this task in the plan. To do this, you will call the \`confirm_task_completion\` tool.`;

const confirmTaskCompletionToolSchema = z.object({
  current_task_completed: z
    .boolean()
    .describe("Whether or not the current task has been completed."),
});

const confirmTaskCompletionTool = {
  name: "confirm_task_completion",
  description: "Whether or not the current task has been completed.",
  schema: confirmTaskCompletionToolSchema,
};

const formatPrompt = (plan: PlanItem[]): string => {
  return systemPrompt.replace("{PLAN_PROMPT}", formatPlanPrompt(plan));
};

export async function progressPlanStep(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config);
  const modelWithTools = model.bindTools([confirmTaskCompletionTool], {
    tool_choice: confirmTaskCompletionTool.name,
  });

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(state.plan),
    },
    ...state.messages,
  ]);
  const toolCall = response.tool_calls?.[0];

  if (!toolCall) {
    throw new Error("Failed to check plan.");
  }

  const isCompleted = (
    toolCall.args as z.infer<typeof confirmTaskCompletionToolSchema>
  ).current_task_completed;

  if (!isCompleted) {
    // Not completed, no changes need to be made
    return {};
  }

  const remainingTask = state.plan.find((p) => !p.completed);
  if (!remainingTask) {
    // No remaining tasks, end the process
    console.log(
      "Found no remaining tasks in the plan during the check plan step.",
    );
    return {};
  }

  return {
    plan: state.plan.map((p) => {
      if (p.index === remainingTask.index) {
        return {
          ...p,
          completed: true,
        };
      }
      return p;
    }),
  };
}
