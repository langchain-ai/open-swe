import { z } from "zod";
import { GraphConfig, GraphState, PlanItem } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import { AIMessage, isHumanMessage } from "@langchain/core/messages";
import { formatPlanPrompt } from "../utils/plan-prompt.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { getMessageString } from "../utils/message/content.js";
import {
  removeFirstHumanMessage,
  removeLastTaskMessages,
} from "../utils/message/modify-array.js";
import { Command } from "@langchain/langgraph";

const logger = createLogger(LogLevel.INFO, "SummarizeTaskSteps");

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You've been given a task to summarize the messages in your conversation history. You just completed a task in your plan, and can now summarize/condense all of the messages in your conversation history which were relevant to that task.
You do not want to keep the entire conversation history, but instead you want to keep the most relevant and important snippets for future context.

{PLAN_PROMPT}

You MUST adhere to the following criteria when summarizing the conversation history:
- Retain context such as file paths, versions, and installed software which future iterations will find useful.
  - It is very important to include the file paths of files you've already searched for, along with a description of the file's contents, inside a 'Codebase files and descriptions' section, so that future steps can reuse this information, and will not need to search through the codebase for files again.
  - Consider including a section titled 'Key repository insights and learnings' which may include information, insights and learnings you've discovered while completing the task.
    - This section should be concise, but still including enough information so following steps will not repeat any mistakes or go down rabbit holes which you already know about.
  - If changes were made to the repository during this task, ensure you include a section titled 'Repository modifications summary' which should include a short description of the task it completed, how it did so, and every change it made to the codebase during this task.
    - Do not include the actual changes you made, but rather high level bullet points containing context and descriptions on the modifications made.
- Do not retain any full code snippets.
- Do not retain any full file contents.
- Ensure your summary is concise, but useful for future context.
- If the conversation history contains any key insights or learnings, ensure you retain those.

With all of this in mind, please carefully summarize and condense the following conversation history. Ensure you pass this condensed context to the \`condense_task_context\` tool.
`;

const formatPrompt = (plan: PlanItem[]): string =>
  systemPrompt.replace(
    "{PLAN_PROMPT}",
    formatPlanPrompt(plan, { useLastCompletedTask: true }),
  );

const condenseContextToolSchema = z.object({
  context: z
    .string()
    .describe(
      "The condensed context from the conversation history relevant to the recently completed task.",
    ),
});
const condenseContextTool = {
  name: "condense_task_context",
  description:
    "Condense the conversation history into a concise summary, while still retaining the most relevant and important snippets.",
  schema: condenseContextToolSchema,
};

export async function summarizeTaskSteps(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const model = await loadModel(config, Task.SUMMARIZER);
  const modelWithTools = model.bindTools([condenseContextTool], {
    tool_choice: condenseContextTool.name,
  });

  const firstUserMessage = state.messages.find(isHumanMessage);

  const conversationHistoryStr = `Here is the full conversation history for the task after the user's request.
This history includes any previous summarization/condensation of the conversation history. Ensure you do NOT summarize those messages, or duplicate any information present in them, but do use them as context so you know what has already been seen and summarized.

${removeFirstHumanMessage(state.messages).map(getMessageString).join("\n")}

Given this full conversation history please generate a concise, and useful summary of the conversation history for this task. Ensure you pass this condensed context to the \`condense_task_context\` tool.`;

  logger.info(`Summarizing task steps...`);
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
    throw new Error("Failed to generate plan");
  }

  const removedMessages = removeLastTaskMessages(state.messages);
  logger.info(`Removing ${removedMessages.length} message(s) from state.`);

  const allTasksCompleted = state.plan.every((p) => p.completed);

  // Ensure all tool calls are removed from the message.
  delete response.tool_call_chunks;
  delete response.tool_calls;
  delete response.invalid_tool_calls;

  const messageWithoutToolCall = new AIMessage({
    ...response,
    content:
      "Condensed Task Context:\n\n" +
      (toolCall.args as z.infer<typeof condenseContextToolSchema>).context,
    additional_kwargs: {
      ...response.additional_kwargs,
      summary_message: true,
    },
  });

  const newMessagesStateUpdate = [...removedMessages, messageWithoutToolCall];

  if (!allTasksCompleted) {
    return new Command({
      goto: "generate-action",
      update: {
        messages: newMessagesStateUpdate,
      },
    });
  }

  return new Command({
    goto: "generate-conclusion",
    update: {
      messages: newMessagesStateUpdate,
    },
  });
}
