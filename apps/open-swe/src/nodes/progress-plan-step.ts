import { z } from "zod";
import { createLogger, LogLevel } from "../utils/logger.js";
import { GraphConfig, GraphState, PlanItem } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import { formatPlanPrompt } from "../utils/plan-prompt.js";
import { Command } from "@langchain/langgraph";
import {
  getMessageContentString,
  getMessageString,
} from "../utils/message/content.js";
import { isHumanMessage } from "@langchain/core/messages";
import { removeFirstHumanMessage } from "../utils/message/modify-array.js";

const logger = createLogger(LogLevel.INFO, "ProgressPlanStep");

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

In your workflow, you generate a plan, then act on said plan. It may take many actions to complete a single step, or a single action to complete the step.

Here is the plan, along with the summaries of each completed task:

{PLAN_PROMPT}

Analyze the tasks you've completed, the tasks which are remaining, and the current task you just took an action on. In addition to this, you're also provided the full conversation history between you and the user. All of the messages in this conversation are from the previous steps/actions you've taken, and any user input.

Take all of this information, and determine whether or not you have completed this task in the plan. Be careful to not mark a task as completed if it is not, this can cause cascading issues in the workflow.
If you determine a task has been completed, you should call the \`confirm_task_completion\` tool. If you do NOT think the current task has been completed, do not call the tool and instead respond with \`not completed.\`.`;

const confirmTaskCompletionToolSchema = z.object({
  reasoning: z
    .string()
    .describe("Reasoning for whether or not the task has been completed."),
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
  const modelWithTools = model.bindTools([confirmTaskCompletionTool], {
    tool_choice: "auto",
  });

  const firstUserMessage = state.messages.find(isHumanMessage);

  const conversationHistoryStr = `Here is the full conversation history after the user's request:
  
${removeFirstHumanMessage(state.messages).map(getMessageString).join("\n")}

Take all of this information, and determine whether or not you have completed this task in the plan. Be careful to not mark a task as completed if it is not, this can cause cascading issues in the workflow.
If you determine a task has been completed, you should call the \`confirm_task_completion\` tool. If you do NOT think the current task has been completed, do not call the tool and instead respond with \`not completed.\`.

ENSURE YOU ONLY CALL THE \`confirm_task_completion\` TOOL IF YOU DETERMINE THE CURRENT TASK HAS BEEN COMPLETED, OR RESPOND WITH 'not completed.'. DO NOT TAKE ANY OTHER ACTION.`;

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
    logger.info(
      "Current task has not been completed, as no tool call was generated. Progressing to the next action.",
      {
        responseContent: getMessageContentString(response.content),
      },
    );
    return new Command({ goto: "generate-action" });
  }

  const isCompleted = (
    toolCall.args as z.infer<typeof confirmTaskCompletionToolSchema>
  ).current_task_completed;

  if (!isCompleted) {
    logger.info(
      "Current task has not been completed. Progressing to the next action.",
      {
        reasoning: toolCall.args.reasoning,
      },
    );
    return new Command({ goto: "generate-action" });
  }

  // This should in theory never happen, but ensure we route properly if it does.
  const remainingTask = state.plan.find((p) => !p.completed);
  if (!remainingTask) {
    logger.info(
      "Found no remaining tasks in the plan during the check plan step. Continuing to the conclusion generation step.",
    );
    return new Command({ goto: "generate-conclusion" });
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
