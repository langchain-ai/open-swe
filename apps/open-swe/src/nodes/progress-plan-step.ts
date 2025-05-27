import { z } from "zod";
import { createLogger, LogLevel } from "../utils/logger.js";
import { GraphConfig, GraphState, PlanItem } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import { formatPlanPrompt } from "../utils/plan-prompt.js";
import { Command } from "@langchain/langgraph";
import { getMessageString } from "../utils/message/content.js";
import { isHumanMessage } from "@langchain/core/messages";
import { removeFirstHumanMessage } from "../utils/message/modify-array.js";

const logger = createLogger(LogLevel.INFO, "ProgressPlanStep");

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

In your workflow, you generate a plan, then act on said plan. It may take many actions to complete a single step, or a single action to complete the step.

Here is the plan, along with the summaries of each completed task:

{PLAN_PROMPT}

Analyze the tasks you've completed, the tasks which are remaining, and the current task you just took an action on. In addition to this, you're also provided the full conversation history between you and the user. All of the messages in this conversation are from the previous steps/actions you've taken, and any user input.

Take all of this information, and determine whether or not you have completed this task in the plan.
Once you've determined the status of the current task, call the \`set_task_status\` tool.
`;

const setTaskStatusToolSchema = z.object({
  reasoning: z
    .string()
    .describe("Reasoning for the status of the current task."),
  task_status: z
    .enum(["completed", "not_completed"])
    .describe("The status of the current task."),
});

const setTaskStatusTool = {
  name: "set_task_status",
  description: "The status of the current task.",
  schema: setTaskStatusToolSchema,
};

const formatPrompt = (plan: PlanItem[]): string => {
  return systemPrompt.replace(
    "{PLAN_PROMPT}",
    formatPlanPrompt(plan, { includeSummaries: true }),
  );
};

export async function progressPlanStep(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const model = await loadModel(config, Task.PROGRESS_PLAN_CHECKER);
  const modelWithTools = model.bindTools([setTaskStatusTool], {
    tool_choice: setTaskStatusTool.name,
  });

  const firstUserMessage = state.messages.find(isHumanMessage);

  const conversationHistoryStr = `Here is the full conversation history after the user's request:
  
${removeFirstHumanMessage(state.messages).map(getMessageString).join("\n")}

Take all of this information, and determine whether or not you have completed this task in the plan.
Once you've determined the status of the current task, call the \`set_task_status\` tool.`;

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(state.plan),
    },
    ...(firstUserMessage ? [firstUserMessage] : []),
    {
      role: "user",
      content: conversationHistoryStr,
    },
  ]);
  const toolCall = response.tool_calls?.[0];

  if (!toolCall) {
    throw new Error(
      "Failed to generate a tool call when checking task status.",
    );
  }

  const isCompleted =
    (toolCall.args as z.infer<typeof setTaskStatusToolSchema>).task_status ===
    "completed";
  const currentTask = state.plan.filter((p) => !p.completed)?.[0];
  const toolMessage = {
    role: "tool",
    tool_call_id: toolCall.id,
    content: `Saved task status as ${
      toolCall.args.task_status
    } for task ${currentTask?.plan || "unknown"}`,
    name: toolCall.name,
  };

  if (!isCompleted) {
    logger.info(
      "Current task has not been completed. Progressing to the next action.",
      {
        reasoning: toolCall.args.reasoning,
      },
    );
    return new Command({
      goto: "generate-action",
      update: { messages: [response, toolMessage] },
    });
  }

  // This should in theory never happen, but ensure we route properly if it does.
  const remainingTask = state.plan.find((p) => !p.completed);
  if (!remainingTask) {
    logger.info(
      "Found no remaining tasks in the plan during the check plan step. Continuing to the conclusion generation step.",
    );
    return new Command({
      goto: "generate-conclusion",
      update: { messages: [response, toolMessage] },
    });
  }

  logger.info("Task marked as completed. Routing to task summarization step.", {
    remainingTask: {
      ...remainingTask,
      completed: true,
    },
  });

  return new Command({
    goto: "summarize-task-steps",
    update: {
      messages: [response, toolMessage],
      plan: state.plan.map((p) => {
        if (p.index === remainingTask.index) {
          return {
            ...p,
            completed: true,
          };
        }
        return p;
      }),
    },
  });
}
