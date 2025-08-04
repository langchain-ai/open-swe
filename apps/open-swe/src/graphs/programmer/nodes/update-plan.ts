import { v4 as uuidv4 } from "uuid";
import {
  GraphState,
  GraphConfig,
  PlanItem,
  GraphUpdate,
  CustomRules,
} from "@open-swe/shared/open-swe/types";
import {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { z } from "zod";
import {
  getActiveTask,
  updateTaskPlanItems,
} from "@open-swe/shared/open-swe/tasks";
import {
  AIMessage,
  BaseMessage,
  isAIMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { getMessageString } from "../../../utils/message/content.js";
import { formatPlanPrompt } from "../../../utils/plan-prompt.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { createUpdatePlanToolFields } from "@open-swe/shared/open-swe/tools";
import { formatCustomRulesPrompt } from "../../../utils/custom-rules.js";
import { trackCachePerformance } from "../../../utils/caching.js";
import { getModelManager } from "../../../utils/llms/model-manager.js";
import { addTaskPlanToIssue } from "../../../utils/github/issue-task.js";

const logger = createLogger(LogLevel.INFO, "UpdatePlanNode");

const systemPrompt = `You are operating as an agentic coding assistant built by LangChain. You've decided that the current plan you're working through needs to be updated.
To aid in this process, you've generated some reasoning and additional context into which plan steps you should update, remove, or whether to add new step(s).

Here is the user's initial request which you used to generate the initial plan:
{USER_REQUEST}

Here is the full plan you generated, which should have changes made to it:
{PLAN}

Here is the reasoning and context you generated for which plan steps to update, remove, or add:
{REASONING}

Given this context, update, remove or add plan steps as needed.

You MUST adhere to the following criteria when generating the plan:
- Make as few changes as possible to the tasks, while still following the users request.
- You are only allowed to update plan items which are remaining, including the current task. Plan items which have already been completed are not allowed to be modified.
- The user will provide the full conversation history which led up to your deciding you need to update the plan. Use this conversation as context when making changes.
- The plan items listed above will include:
  - The index of the plan item. This is the order in which the plan items should be executed in.
  - The actual plan of the individual task.
  - If it's been completed, it will include a summary of the completed task.
- To update the plan, you MUST pass every updated/added/untouched plan item to the \`update_plan\` tool.
  - These will replace all of the existing plan items.
  - This means you still need to include all of the unmodified plan items in the \`update_plan\` tool call.
- You should call the \`update_plan\` tool, passing in each plan item in the order they should be executed in.
- To remove an item from the plan, you should not include it in the \`update_plan\` tool call.

{CUSTOM_RULES}

With all of this in mind, please call the \`update_plan\` tool with the updated plan.
`;

const updatePlanToolSchema = z.object({
  plan: z
    .array(z.string())
    .describe(
      "The updated, or new plan, including any changes to the plan items, as well as any new plan items you've added.",
    ),
});

const updatePlanTool = {
  name: "update_plan",
  description:
    "The updated plan, including any changes to the plan items, as well as any new plan items you've added, and the unchanged plan items. This should NOT include any of the completed plan items.",
  schema: updatePlanToolSchema,
};

const updatePlanReasoningTool = createUpdatePlanToolFields();

const formatSystemPrompt = (
  userRequest: string,
  reasoning: string,
  planItems: PlanItem[],
  customRules?: CustomRules,
) => {
  return systemPrompt
    .replace("{USER_REQUEST}", userRequest)
    .replace("{PLAN}", formatPlanPrompt(planItems, { includeSummaries: true }))
    .replace("{REASONING}", reasoning)
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(customRules));
};

const formatUserMessage = (messages: BaseMessage[]): string => {
  return `Here is the full conversation history you should use as context when making changes to the plan:
  
${messages.map(getMessageString).join("\n")}`;
};

function removeUncalledTools(lastMessage: AIMessage): AIMessage {
  if (!lastMessage.tool_calls?.length || lastMessage.tool_calls?.length === 1) {
    // check for no tool calls. will never happen, but need for type safety
    // only one tool call, this is the update plan tool call. no-op
    return lastMessage;
  }

  const updatePlanReasoningToolCall = lastMessage.tool_calls?.find(
    (tc) => tc.name === updatePlanReasoningTool.name,
  );
  if (!updatePlanReasoningToolCall) {
    throw new Error("Update plan reasoning tool call not found.");
  }

  // Return the last message, only changing the tool calls to only include the update plan reasoning tool call.
  return new AIMessage({
    ...lastMessage,
    tool_calls: [updatePlanReasoningToolCall],
  });
}

export async function updatePlan(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const lastMessage = state.internalMessages[state.internalMessages.length - 1];

  if (!lastMessage || !isAIMessage(lastMessage) || !lastMessage.id) {
    throw new Error("Last message was not an AI message");
  }

  const updatePlanToolCall = lastMessage.tool_calls?.find(
    (tc) => tc.name === updatePlanReasoningTool.name,
  );
  const updatePlanToolCallId = updatePlanToolCall?.id;
  const updatePlanToolCallArgs = updatePlanToolCall?.args as z.infer<
    typeof updatePlanReasoningTool.schema
  >;
  if (!updatePlanToolCall || !updatePlanToolCallId || !updatePlanToolCallArgs) {
    throw new Error("Update plan with reasoning tool call not found.");
  }

  logger.info("Updating plan", {
    ...updatePlanToolCall,
  });

  const model = await loadModel(config, LLMTask.PROGRAMMER);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(
    config,
    LLMTask.PROGRAMMER,
  );
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.PROGRAMMER,
  );
  const modelWithTools = model.bindTools([updatePlanTool], {
    tool_choice: updatePlanTool.name,
    ...(modelSupportsParallelToolCallsParam
      ? {
          parallel_tool_calls: false,
        }
      : {}),
  });

  const activeTask = getActiveTask(state.taskPlan);
  const request = activeTask.request;
  const activePlanItems = activeTask.planRevisions.find(
    (pr) => pr.revisionIndex === activeTask.activeRevisionIndex,
  )?.plans;
  if (!activePlanItems?.length) {
    throw new Error("No active plan items found.");
  }

  const systemPrompt = formatSystemPrompt(
    request,
    updatePlanToolCallArgs.update_plan_reasoning,
    activePlanItems,
  );
  const userMessage = formatUserMessage(state.internalMessages);

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: systemPrompt,
    },
    {
      role: "user",
      content: userMessage,
    },
  ]);
  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("No tool call found.");
  }

  const { plan } = toolCall.args as z.infer<typeof updatePlanToolSchema>;
  const completedPlanItems = activePlanItems.filter((item) => item.completed);
  const totalCompletedPlanItems = completedPlanItems.length;
  const newPlanItems: PlanItem[] = [
    ...completedPlanItems,
    ...plan.map((p, index) => ({
      index: totalCompletedPlanItems + index,
      plan: p,
      completed: false,
      summary: undefined,
    })),
  ];

  const newTaskPlan = updateTaskPlanItems(
    state.taskPlan,
    activeTask.id,
    newPlanItems,
    "agent",
  );
  // Update the github issue to reflect the changes in the plan
  await addTaskPlanToIssue(
    {
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
    },
    config,
    newTaskPlan,
  );

  const toolMessage = new ToolMessage({
    id: uuidv4(),
    tool_call_id: updatePlanToolCallId,
    content:
      "Successfully updated the plan. The complete updated plan items are as follow:\n\n" +
      newPlanItems
        .map(
          (p) =>
            `<plan-item completed="${p.completed}" index="${p.index}">${p.plan}</plan-item>`,
        )
        .join("\n"),
  });

  return {
    messages: [removeUncalledTools(lastMessage), toolMessage],
    internalMessages: [removeUncalledTools(lastMessage), toolMessage],
    taskPlan: newTaskPlan,
    tokenData: trackCachePerformance(response, modelName),
  };
}
