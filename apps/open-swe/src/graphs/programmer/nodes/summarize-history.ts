import { v4 as uuidv4 } from "uuid";
import {
  GraphConfig,
  GraphState,
  GraphUpdate,
  PlanItem,
} from "@open-swe/shared/open-swe/types";
import { loadModel } from "../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import {
  AIMessage,
  BaseMessage,
  RemoveMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { formatPlanPrompt } from "../../../utils/plan-prompt.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { getMessageString } from "../../../utils/message/content.js";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { createConversationHistorySummaryToolFields } from "@open-swe/shared/open-swe/tools";
import { formatUserRequestPrompt } from "../../../utils/user-request.js";
import { getMessagesSinceLastSummary } from "../../../utils/tokens.js";
import { trackCachePerformance } from "../../../utils/caching.js";
import { getModelManager } from "../../../utils/llms/model-manager.js";

const SINGLE_USER_REQUEST_PROMPT = `Here is the user's request:
<user_request>
{USER_REQUEST}
</user_request>`;

const USER_SENDING_FOLLOWUP_PROMPT = `Here is the user's initial request:
<user_initial_request>
{USER_REQUEST}
</user_initial_request>

And here is the user's followup request you're now processing:
<user_followup_request>
{USER_FOLLOWUP_REQUEST}
</user_followup_request>`;

const taskSummarySysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

<role>
Context Extraction Assistant
</role>

<primary_objective>
Your sole objective in this task is to extract the highest quality/most relevant context from the conversation history below.
</primary_objective>

<objective_information>
You're nearing the total number of input tokens you can accept, so you must extract the highest quality/most relevant pieces of information from your conversation history.
This context will then overwrite the conversation history presented below. Because of this, ensure the context you extract is only the most important information to your overall goal.
To aid with this, you'll be provided with the user's request, as well as all of the tasks in the plan you generated to fulfil the user's request. Additionally, if a task has already been completed you'll be provided with the summary of the steps taken to complete it.
</objective_information>

{USER_REQUEST_PROMPT}

Here is the full list of tasks in the plan you're in the middle of, as well as the summary of the completed tasks:
<tasks_and_summaries>
{PLAN_PROMPT}
</tasks_and_summaries>

<instructions>
The conversation history below will be replaced with the context you extract in this step. Because of this, you must do your very best to extract and record all of the most important context from the conversation history.
You want to ensure that you don't repeat any actions you've already completed (e.g. file search operations, checking codebase information, etc.), so the context you extract from the conversation history should be focused on the most important information to your overall goal.

You MUST adhere to the following criteria when extracting the most important context from the conversation history:
  - Include full file paths for all relevant files to the users request & tasks.
  - Include file summaries/snippets from the relevant files. Avoid including entire files as you're trying to condense the conversation history.
  - Include insights, and learnings you've discovered about the codebase or specific files while completing the task.
  - Only record information once, and avoid duplications. Duplicate information or actions in the conversation history should be merged into a single entry.
</instructions>

Here is the full conversation history you'll be extracting context from, to then replace. Carefully read over it all, and think deeply about what information is most important to your overall goal that should be saved:
<conversation_history>
{CONVERSATION_HISTORY}
</conversation_history>

With all of this in mind, please carefully read over the entire conversation history, and extract the most important and relevant context to replace it so that you can free up space in the conversation history.
Respond ONLY with the extracted context. Do not include any additional information, or text before or after the extracted context.
`;

const logger = createLogger(LogLevel.INFO, "SummarizeConversationHistory");

const formatPrompt = (inputs: {
  messages: BaseMessage[];
  plan: PlanItem[];
  conversationHistoryToSummarize: BaseMessage[];
}): string => {
  return taskSummarySysPrompt
    .replace(
      "{PLAN_PROMPT}",
      formatPlanPrompt(inputs.plan, {
        useLastCompletedTask: true,
        includeSummaries: true,
      }),
    )
    .replace(
      "{USER_REQUEST_PROMPT}",
      formatUserRequestPrompt(
        inputs.messages,
        SINGLE_USER_REQUEST_PROMPT,
        USER_SENDING_FOLLOWUP_PROMPT,
      ),
    )
    .replace(
      "{CONVERSATION_HISTORY}",
      inputs.conversationHistoryToSummarize.map(getMessageString).join("\n"),
    );
};

function createSummaryMessages(summary: string): BaseMessage[] {
  const dummySummarizeHistoryToolName =
    createConversationHistorySummaryToolFields().name;
  const dummySummarizeHistoryToolCallId = uuidv4();
  return [
    new AIMessage({
      id: uuidv4(),
      content:
        "Looks like I'm running out of tokens. I'm going to summarize the conversation history to free up space.",
      tool_calls: [
        {
          id: dummySummarizeHistoryToolCallId,
          name: dummySummarizeHistoryToolName,
          args: {
            reasoning:
              "I'm running out of tokens. I'm going to summarize all of the messages since my last summary message to free up space.",
          },
        },
      ],
      additional_kwargs: {
        summary_message: true,
      },
    }),
    new ToolMessage({
      id: uuidv4(),
      tool_call_id: dummySummarizeHistoryToolCallId,
      content: summary,
      additional_kwargs: {
        summary_message: true,
      },
    }),
  ];
}

export async function summarizeHistory(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, LLMTask.SUMMARIZER);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(
    config,
    LLMTask.SUMMARIZER,
  );

  const plan = getActivePlanItems(state.taskPlan);
  const conversationHistoryToSummarize = await getMessagesSinceLastSummary(
    state.internalMessages,
    {
      excludeHiddenMessages: true,
      excludeCountFromEnd: 20,
    },
  );

  logger.info(
    `Summarizing ${conversationHistoryToSummarize.length} messages in the conversation history...`,
  );

  const response = await model.invoke([
    {
      role: "user",
      content: formatPrompt({
        messages: state.messages,
        plan,
        conversationHistoryToSummarize,
      }),
    },
  ]);

  const summaryString = getMessageContentString(response.content);
  const summaryMessages = createSummaryMessages(summaryString);

  const newInternalMessages = [
    ...conversationHistoryToSummarize.map(
      (m) => new RemoveMessage({ id: m.id ?? "" }),
    ),
    ...summaryMessages,
  ];

  logger.info(
    `Summarized ${conversationHistoryToSummarize.length} messages in the conversation history. Removing and replacing with a summary message.`,
  );

  return {
    messages: summaryMessages,
    internalMessages: newInternalMessages,
    tokenData: trackCachePerformance(response, modelName),
  };
}
