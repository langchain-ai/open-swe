import { Command } from "@langchain/langgraph";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { z } from "zod";
import {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { getMissingMessages } from "../../../utils/github/issue-messages.js";
import { getMessageString } from "../../../utils/message/content.js";
import { isHumanMessage } from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";
import { filterHiddenMessages } from "../../../utils/message/filter-hidden.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { trackCachePerformance } from "../../../utils/caching.js";
import { getModelManager } from "../../../utils/llms/model-manager.js";

const logger = createLogger(LogLevel.INFO, "DetermineNeedsContext");

const SYSTEM_PROMPT = `You are a terminal-based agentic coding assistant built by LangChain that enables natural language interaction with local codebases. You excel at being precise, safe, and helpful in your analysis.

<role>
Context Gathering Assistant - Read-Only Phase
</role>

<primary_objective>
Your sole objective in this step is to determine whether or not the user's followup request requires additional context to be gathered in order to update the plan/add additional steps to the plan.
</primary_objective>

<instructions>
You're provided with these main pieces of information:
- **Conversation history**: This is the full conversation history between you, the user, and including any actions you took while gathering context.
- **Context gathering notes**: This is the notes you took while gathering context. Includes the most relevant context you discovered while gathering context for the plan.
- **Proposed plan**: This is the plan you generated for the user's request, which the user is likely trying to follow up on (e.g. modify it in some way, or add new step(s)).
- **User followup request**: This is the specific followup request made by the user (the conversation history will also include this). This is the message you should look at when determining whether or not you need to gather more context before you can update the proposed plan.

Given this information, carefully read over it all and determine whether or not you need to gather more context before you can update the proposed plan.
You may already have enough context from the conversation history and the actions you executed, or the notes you took while gathering context, to update the proposed plan.

The state of the repository has NOT changed since you last gathered context & proposed the plan.

To make your decision, you must first provide reasoning for why you need to gather more context, or why you already have enough context. Then, make your decision.
Both of these steps should be executed by calling the \`determine_context\` tool.
</instructions>

<conversation_history>
{CONVERSATION_HISTORY}
</conversation_history>

<context_gathering_notes>
{CONTEXT_GATHERING_NOTES}
</context_gathering_notes>

<proposed_plan>
{PROPOSED_PLAN}
</proposed_plan>

<user_followup_request>
{USER_FOLLOWUP_REQUEST}
</user_followup_request>

<determine_context>
Once again, with all of the above information, determine whether or not you need to gather more context before you can accurately update the proposed plan.
</determine_context>
`;

function formatSystemPrompt(state: PlannerGraphState): string {
  const formattedConversationHistoryPrompt = state.messages
    .map(getMessageString)
    .join("\n");
  const formattedProposedPlan = state.proposedPlan
    .map((p, index) => `  ${index + 1}. ${p}`)
    .join("\n");
  const userFollowupRequestMsg = state.messages.findLast(isHumanMessage);
  if (!userFollowupRequestMsg) {
    throw new Error("User followup request not found.");
  }
  const userFollowupRequestStr = getMessageContentString(
    userFollowupRequestMsg.content,
  );

  return SYSTEM_PROMPT.replace(
    "{CONVERSATION_HISTORY}",
    formattedConversationHistoryPrompt,
  )
    .replace("{CONTEXT_GATHERING_NOTES}", state.contextGatheringNotes)
    .replace("{PROPOSED_PLAN}", formattedProposedPlan)
    .replace("{USER_FOLLOWUP_REQUEST}", userFollowupRequestStr);
}

const determineContextSchema = z.object({
  reasoning: z
    .string()
    .describe(
      "The reasoning for whether or not you have enough context to update the proposed plan, or why you need to gather more context before you can update the proposed plan.",
    ),
  decision: z
    .enum(["have_context", "need_context"])
    .describe(
      "Whether or not you have enough context to update the proposed plan, or if you need to gather more context before you can accurately update the proposed plan. " +
        "If you have enough context to update the plan, respond with 'have_context'. " +
        "If you need to gather more context, respond with 'need_context'.",
    ),
});
const determineContextTool = {
  name: "determine_context",
  description:
    "Determine whether or not you have enough context to update the proposed plan, or if you need to gather more context before you can accurately update the proposed plan.",
  schema: determineContextSchema,
};

export async function determineNeedsContext(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const [missingMessages, model] = await Promise.all([
    getMissingMessages(state, config),
    loadModel(config, LLMTask.ROUTER),
  ]);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(config, LLMTask.ROUTER);
  if (!missingMessages.length) {
    throw new Error(
      "Can not determine if more context is needed if there are no missing messages.",
    );
  }
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.ROUTER,
  );
  const modelWithTools = model.bindTools([determineContextTool], {
    tool_choice: determineContextTool.name,
    ...(modelSupportsParallelToolCallsParam
      ? {
          parallel_tool_calls: false,
        }
      : {}),
  });

  const response = await modelWithTools.invoke([
    {
      role: "user",
      content: formatSystemPrompt({
        ...state,
        messages: [...filterHiddenMessages(state.messages), ...missingMessages],
      }),
    },
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("No tool call found.");
  }

  const commandUpdate: PlannerGraphUpdate = {
    messages: missingMessages,
    tokenData: trackCachePerformance(response, modelName),
  };

  const shouldGatherContext =
    (toolCall.args as z.infer<typeof determineContextSchema>).decision ===
    "need_context";
  logger.info(
    "Determined whether or not additional context is needed to update plan",
    {
      ...toolCall.args,
    },
  );

  return new Command({
    goto: shouldGatherContext
      ? "generate-plan-context-action"
      : "generate-plan",
    update: commandUpdate,
  });
}
