import { loadModel, Task } from "../../../utils/load-model.js";
import { shellTool } from "../../../tools/index.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { GraphConfig } from "../../../types.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { getMessageContentString } from "../../../utils/message/content.js";
import { getUserRequest } from "../../../utils/user-request.js";
import { isHumanMessage } from "@langchain/core/messages";
import { formatFollowupMessagePrompt } from "../utils/followup-prompt.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.
{FOLLOWUP_MESSAGE_PROMPT}

You MUST adhere to the following criteria when gathering context for the plan:
- You must ONLY take read actions to gather context. Write actions are NOT allowed.
- Keep in mind you are only permitted to make a maximum of 6 tool calls to gather all your context. Ensure each action is of high quality, and targeted to aid in generating a plan.
- Always use \`rg\` instead of \`grep/ls -R\` because it is much faster and respects gitignore.
  - Always use glob patterns when searching with \`rg\` for specific file types. For example, to search for all TSX files, use \`rg -i star -g **/*.tsx project-directory/\`. This is because \`rg\` does not have built in file types for every language.
- If you determine you've gathered enough context to generate a plan, simply reply with 'done' and do NOT call any tools.
- Not generating a tool call will be interpreted as an indication that you've gathered enough context to generate a plan.


The user's request is as follows. Ensure you generate your plan in accordance with the user's request.
{USER_REQUEST}
`;

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = state.messages.filter(isHumanMessage).length > 1;
  const userRequest = getUserRequest(state.messages);

  return systemPrompt
    .replace(
      "{FOLLOWUP_MESSAGE_PROMPT}",
      isFollowup ? formatFollowupMessagePrompt(state.plan, state.messages) : "",
    )
    .replace("{USER_REQUEST}", userRequest);
}

export async function generateAction(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [shellTool];
  const modelWithTools = model.bindTools(tools, { tool_choice: "auto" });

  const response = await modelWithTools
    .withConfig({ tags: ["nostream"] })
    .invoke([
      {
        role: "system",
        content: formatSystemPrompt(state),
      },
      ...state.plannerMessages,
    ]);

  logger.info("Generated planning message", {
    ...(getMessageContentString(response.content) && {
      content: getMessageContentString(response.content),
    }),
    ...(response.tool_calls?.[0] && {
      name: response.tool_calls?.[0].name,
      args: response.tool_calls?.[0].args,
    }),
  });

  return {
    plannerMessages: [response],
  };
}
