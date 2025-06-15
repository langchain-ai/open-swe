import { z } from "zod";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { loadModel, Task } from "../../../utils/load-model.js";
import { getMessageString } from "../../../utils/message/content.js";
import { getUserRequest } from "../../../utils/user-request.js";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You've been given a task to summarize the messages in your conversation history. You just finished gathering context to be used when generating an development plan to address the user's request.
You do not want to keep the entire conversation history, but instead you want to keep the most relevant and important snippets for future context

You MUST adhere to the following criteria when summarizing the conversation history:
- Retain context such as file paths, versions, and installed software.
  - It is very important to include the file paths of files you've already searched for, along with a description of the file's contents, inside a 'Codebase files and descriptions' section, so that future steps can reuse this information, and will not need to search through the codebase for files again.
- Consider including a section titled 'Key repository insights and learnings' which may include information, insights and learnings you've discovered while gathering context for the user's request.
  - This section should be concise, but still including enough information so following steps will not repeat any mistakes or go down rabbit holes which you already know about.
- Do not retain any full code snippets.
- Do not retain any full file contents.
- Ensure your summary is concise, but useful for future context.

Here is the user's request
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
  const model = await loadModel(config, Task.SUMMARIZER);
  const modelWithTools = model.bindTools([condenseContextTool], {
    tool_choice: condenseContextTool.name,
    parallel_tool_calls: false,
  });

  const userRequest = getUserRequest(state.internalMessages);
  const conversationHistoryStr = `Here is the full conversation history:

${state.plannerMessages.map(getMessageString).join("\n")}`;

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(userRequest || "No user request provided."),
    },
    {
      role: "user",
      content: conversationHistoryStr,
    },
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("Failed to generate plan");
  }

  return {
    messages: [response],
    planContextSummary: toolCall.args.context,
  };
}
