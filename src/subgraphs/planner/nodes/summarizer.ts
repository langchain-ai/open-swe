import { z } from "zod";
import { GraphConfig } from "../../../types.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { loadModel, Task } from "../../../utils/load-model.js";
import { isHumanMessage, ToolMessage } from "@langchain/core/messages";
import { getMessageContentString } from "../../../utils/message-content.js";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You've been given a task to summarize the messages in your conversation history. You just finished gathering context to be used when generating an development plan to address the user's request.
You do not want to keep the entire conversation history, but instead you want to keep the most relevant and important snippets for future context

You MUST adhere to the following criteria when summarizing the conversation history:
- Retain context such as file paths, versions, and installed software.
- Do not retain any full code snippets.
- Do not retain any full file contents.
- Ensure your summary is concise, but useful for future context.

Here is the user's initial request
## User request:
{USER_REQUEST}

With all of this in mind, please carefully summarize and condense the following conversation history. Ensure you pass this condensed context to the \`condense_planning_context\` tool.
`;

const formatPrompt = (userRequest: string): string =>
  systemPrompt.replace("{USER_REQUEST}", userRequest);

const condenseContextToolSchema = z.object({
  context: z
    .string()
    .describe("The condensed context to be used when generating a plan."),
});
const condenseContextTool = {
  name: "condense_planning_context",
  description:
    "Condense the conversation history into a concise summary, while still retaining the most relevant and important snippets.",
  schema: condenseContextToolSchema,
};

export async function summarizer(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.PLANNER);
  const modelWithTools = model.bindTools([condenseContextTool], {
    tool_choice: condenseContextTool.name,
  });

  const firstUserMessage = state.messages.find(isHumanMessage);

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(
        getMessageContentString(
          firstUserMessage?.content ?? "No user request provided.",
        ),
      ),
    },
    ...state.plannerMessages,
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("Failed to generate plan");
  }

  const toolMessage = new ToolMessage({
    tool_call_id: toolCall.id ?? "",
    name: toolCall.name,
    content: `Successfully summarized planning context.`,
  });

  return {
    messages: [response, toolMessage],
  };
}
