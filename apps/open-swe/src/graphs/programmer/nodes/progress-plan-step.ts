import { z } from "zod";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  GraphConfig,
  GraphState,
  GraphUpdate,
  PlanItem,
} from "@open-swe/shared/open-swe/types";
import { loadModel, Task } from "../../../utils/load-model.js";
import { formatPlanPrompt } from "../../../utils/plan-prompt.js";
import { Command } from "@langchain/langgraph";
import { getMessageString } from "../../../utils/message/content.js";
import { removeFirstHumanMessage } from "../../../utils/message/modify-array.js";
import { getUserRequest } from "../../../utils/user-request.js";
import {
  completePlanItem,
  getActivePlanItems,
  getActiveTask,
} from "@open-swe/shared/open-swe/tasks";
import {
  getCurrentPlanItem,
  getRemainingPlanItems,
} from "../../../utils/current-task.js";
import { ToolMessage } from "@langchain/core/messages";
import { addTaskPlanToIssue } from "../../../utils/github/issue-task.js";

const logger = createLogger(LogLevel.INFO, "ProgressPlanStep");

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

In your workflow, you generate a plan, then act on said plan. It may take many actions to complete a single step, or a single action to complete the step.

Here is the plan, along with the summaries of each completed task:
{PLAN_PROMPT}

Analyze the tasks you've completed, the tasks which are remaining, and the current task you just took an action on.
In addition to this, you're also provided the full conversation history between you and the user. All of the messages in this conversation are from the previous steps/actions you've taken, and any user input.

Take all of this information, and determine whether or not you have completed this task in the plan.
Once you've determined the status of the current task, call the \`set_task_status\` tool.
`;

const setTaskStatusToolSchema = z.object({
  reasoning: z
    .string()
    .describe(
      "A concise reasoning summary for the status of the current task, explaining why you think it is completed or not completed.",
    ),
  task_status: z
    .enum(["completed", "not_completed"])
    .describe(
      "The status of the current task, based on the reasoning provided.",
    ),
});

const setTaskStatusTool = {
  name: "set_task_status",
  description:
    "The status of the current task, along with a concise reasoning summary to support the status.",
  schema: setTaskStatusToolSchema,
};

const formatPrompt = (taskPlan: PlanItem[]): string => {
  return systemPrompt.replace(
    "{PLAN_PROMPT}",
    formatPlanPrompt(taskPlan, { includeSummaries: true }),
  );
};

export async function progressPlanStep(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const model = await loadModel(config, Task.PROGRESS_PLAN_CHECKER);
  const modelWithTools = model.bindTools([setTaskStatusTool], {
    tool_choice: setTaskStatusTool.name,
    parallel_tool_calls: false,
  });

  const userRequest = getUserRequest(state.internalMessages, {
    returnFullMessage: true,
  });
  const conversationHistoryStr = `Here is the full conversation history after the user's request:
  
${removeFirstHumanMessage(state.internalMessages).map(getMessageString).join("\n")}

Take all of this information, and determine whether or not you have completed this task in the plan.
Once you've determined the status of the current task, call the \`set_task_status\` tool.`;

  const activePlanItems = getActivePlanItems(state.taskPlan);

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(activePlanItems),
    },
    userRequest,
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
  const currentTask = getCurrentPlanItem(activePlanItems);
  const toolMessage = new ToolMessage({
    tool_call_id: toolCall.id ?? "",
    content: `Saved task status as ${
      toolCall.args.task_status
    } for task ${currentTask?.plan || "unknown"}`,
    name: toolCall.name,
  });

  const newMessages = [response, toolMessage];

  if (!isCompleted) {
    logger.info(
      "Current task has not been completed. Progressing to the next action.",
      {
        reasoning: toolCall.args.reasoning,
      },
    );
    const commandUpdate: GraphUpdate = {
      messages: newMessages,
      internalMessages: newMessages,
    };
    return new Command({
      goto: "generate-action",
      update: commandUpdate,
    });
  }

  // LLM marked as completed, so we need to update the plan to reflect that.
  const updatedPlanTasks = completePlanItem(
    state.taskPlan,
    getActiveTask(state.taskPlan).id,
    currentTask.index,
  );
  // Update the github issue to reflect this task as completed.
  await addTaskPlanToIssue(
    {
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
    },
    config,
    updatedPlanTasks,
  );

  // This should in theory never happen, but ensure we route properly if it does.
  const remainingTask = getRemainingPlanItems(activePlanItems)?.[0];
  if (!remainingTask) {
    logger.info(
      "Found no remaining tasks in the plan during the check plan step. Continuing to the conclusion generation step.",
    );
    const commandUpdate: GraphUpdate = {
      messages: newMessages,
      internalMessages: newMessages,
      // Even though there are no remaining tasks, still mark as completed so the UI reflects that the task is completed.
      taskPlan: updatedPlanTasks,
    };
    return new Command({
      goto: "generate-conclusion",
      update: commandUpdate,
    });
  }

  logger.info("Task marked as completed. Routing to task summarization step.", {
    remainingTask: {
      ...remainingTask,
      completed: true,
    },
  });

  const commandUpdate: GraphUpdate = {
    messages: newMessages,
    internalMessages: newMessages,
    taskPlan: updatedPlanTasks,
  };

  return new Command({
    goto: "summarize-task-steps",
    update: commandUpdate,
  });
}
