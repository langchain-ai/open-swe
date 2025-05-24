import { z } from "zod";
import { GraphConfig, GraphState, GraphUpdate, PlanItem } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import {
  AIMessage,
  BaseMessage,
  isAIMessage,
  isToolMessage,
  RemoveMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { formatPlanPrompt } from "../utils/plan-prompt.js";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You've been given a task to summarize the messages in your conversation history. You just completed a task in your plan, and can now summarize/condense all of the messages in your conversation history which were relevant to that task.
You do not want to keep the entire conversation history, but instead you want to keep the most relevant and important snippets for future context.

{PLAN_PROMPT}

You MUST adhere to the following criteria when summarizing the conversation history:
- Retain context such as file paths, versions, and installed software.
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

function removeLastTaskMessages(messages: BaseMessage[]): BaseMessage[] {
  return messages.map((m) => {
    if (
      m.additional_kwargs?.summary_message ||
      (!isAIMessage(m) && !isToolMessage(m)) ||
      !m.id
    ) {
      return m;
    }
    return new RemoveMessage({ id: m.id });
  });
}

export async function summarizeTaskSteps(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, Task.SUMMARIZER);
  const modelWithTools = model.bindTools([condenseContextTool], {
    tool_choice: condenseContextTool.name,
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
    throw new Error("Failed to generate plan");
  }

  const toolMessage = new ToolMessage({
    tool_call_id: toolCall.id ?? "",
    name: toolCall.name,
    content: `Successfully summarized planning context.`,
    additional_kwargs: {
      summary_message: true,
    },
  });

  return {
    messages: [
      ...removeLastTaskMessages(state.messages),
      new AIMessage({
        ...response,
        additional_kwargs: {
          ...response.additional_kwargs,
          summary_message: true,
        },
      }),
      toolMessage,
    ],
  };
}
