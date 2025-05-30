import { loadModel, Task } from "../../../utils/load-model.js";
import { shellTool } from "../../../tools/index.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { GraphConfig, PlanItem } from "../../../types.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  getMessageContentString,
  getMessageString,
} from "../../../utils/message/content.js";
import { getUserRequest } from "../../../utils/user-request.js";
import { BaseMessage } from "@langchain/core/messages";
import { formatPlanPromptWithSummaries } from "../../../utils/plan-prompt.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

const followupMessagePrompt = `
The user is sending a followup request, so you should assume the most recent message they sent is their request. You are also provided with the full conversation history from their previous request, which you should use as context when generating a plan.

Here is the full list of tasks and task summaries from the previous plan you executed:
{PREVIOUS_PLAN}

Here is the full conversation history from the previous plan you executed:
{PREVIOUS_CONVERSATION_HISTORY}
`;

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.
{FOLLOWUP_MESSAGE_PROMPT}

You MUST adhere to the following criteria when gathering context for the plan:
- You must ONLY take read actions to gather context. Write actions are NOT allowed.
- Keep in mind you are only permitted to make a maximum of 6 tool calls to gather all your context. Ensure each action is of high quality, and targeted to aid in generating a plan.
- Always use \`rg\` instead of \`grep/ls -R\` because it is much faster and respects gitignore.
  - Always use glob patterns when searching with \`rg\` for specific file types. For example, to search for all TSX files, use \`rg -i star -g **/*.tsx project-directory/\`. This is because \`rg\` does not have built in file types for every language.
- If you determine you've gathered enough context to generate a plan, simply reply with 'done' and do NOT call any tools.
- Not generating a tool call will be interpreted as an indication that you've gathered enough context to generate a plan.
- The first user message in this conversation contains the user's request.
`;

function formatFollowupMessagePrompt(
  plan: PlanItem[],
  conversationHistory: BaseMessage[],
): string {
  return followupMessagePrompt
    .replace("{PREVIOUS_PLAN}", formatPlanPromptWithSummaries(plan))
    .replace(
      "{PREVIOUS_CONVERSATION_HISTORY}",
      conversationHistory.slice(0, -1).map(getMessageString).join("\n"),
    );
}

export async function generateAction(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [shellTool];
  const modelWithTools = model.bindTools(tools, { tool_choice: "auto" });

  // If there is only one message it's not a followup.
  const isFollowup = state.messages.length > 1;

  const userRequest = getUserRequest(state.messages, {
    returnFullMessage: true,
  });
  const response = await modelWithTools
    .withConfig({ tags: ["nostream"] })
    .invoke([
      {
        role: "system",
        content: systemPrompt.replace(
          "{FOLLOWUP_MESSAGE_PROMPT}",
          isFollowup
            ? formatFollowupMessagePrompt(state.plan, state.messages)
            : "",
        ),
      },
      userRequest,
      ...state.plannerMessages,
    ]);

  logger.info("Generated planning message", {
    ...(response.tool_calls?.[0] && {
      name: response.tool_calls?.[0].name,
      args: response.tool_calls?.[0].args,
    }),
    ...(getMessageContentString(response.content) && {
      content: getMessageContentString(response.content),
    }),
  });

  return {
    plannerMessages: [response],
  };
}
