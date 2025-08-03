import { BaseMessage } from "@langchain/core/messages";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { z } from "zod";
import {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { getMessageString } from "../../../utils/message/content.js";

export async function createIssueFieldsFromMessages(
  messages: BaseMessage[],
  configurable: GraphConfig["configurable"],
): Promise<{ title: string; body: string }> {
  const model = await loadModel({ configurable }, LLMTask.ROUTER);
  const githubIssueTool = {
    name: "create_github_issue",
    description: "Create a new GitHub issue with the given title and body.",
    schema: z.object({
      title: z
        .string()
        .describe(
          "The title of the issue to create. Should be concise and clear.",
        ),
      body: z
        .string()
        .describe(
          "The body of the issue to create. This should be an extremely concise description of the issue. You should not over-explain the issue, as we do not want to waste the user's time. Do not include any additional context not found in the conversation history.",
        ),
    }),
  };
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    { configurable },
    LLMTask.ROUTER,
  );
  const modelWithTools = model
    .bindTools([githubIssueTool], {
      tool_choice: githubIssueTool.name,
      ...(modelSupportsParallelToolCallsParam
        ? {
            parallel_tool_calls: false,
          }
        : {}),
    })
    .withConfig({ tags: ["nostream"], runName: "create-issue-fields" });

  const prompt = `You're an AI programmer, tasked with taking the conversation history provided below, and creating a new GitHub issue.
Ensure the issue title and body are both clear and concise. Do not hallucinate any information not found in the conversation history.
You should mainly be looking at the human messages as context for the issue.

# Conversation History
${messages.map(getMessageString).join("\n")}

With the above conversation history in mind, please call the ${githubIssueTool.name} tool to create a new GitHub issue based on the user's request.`;

  const result = await modelWithTools.invoke([
    {
      role: "user",
      content: prompt,
    },
  ]);
  const toolCall = result.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("No tool call found in result");
  }
  return toolCall.args as z.infer<typeof githubIssueTool.schema>;
}
