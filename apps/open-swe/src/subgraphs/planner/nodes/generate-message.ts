import { loadModel, Task } from "../../../utils/load-model.js";
import { shellTool } from "../../../tools/index.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { GraphConfig } from "../../../types.js";
import { isHumanMessage } from "@langchain/core/messages";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

Your sole task is to gather context from the repository the user has provided which will be helpful when generating a plan to address the user's request.

You MUST adhere to the following criteria when gathering context for the plan:
- You must ONLY take read actions to gather context. Write actions are NOT allowed.
- Keep in mind you are only permitted to make a maximum of 6 tool calls to gather all your context. Ensure each action is of high quality, and targeted to aid in generating a plan.
- Always use \`rg\` instead of \`grep/ls -R\` because it is much faster and respects gitignore.
  - Always use glob patterns when searching with \`rg\` for specific file types. For example, to search for all TSX files, use \`rg -i star -g **/*.tsx project-directory/\`. This is because \`rg\` does not have built in file types for every language.
- If you determine you've gathered enough context to generate a plan, simply reply with 'done' and do NOT call any tools.
- Not generating a tool call will be interpreted as an indication that you've gathered enough context to generate a plan.
- The first user message in this conversation contains the user's request.
`;

export async function generateAction(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [shellTool];
  const modelWithTools = model.bindTools(tools, { tool_choice: "auto" });

  const firstUserMessage = state.messages.find(isHumanMessage);

  const response = await modelWithTools
    .withConfig({ tags: ["nostream"] })
    .invoke([
      {
        role: "system",
        content: systemPrompt,
      },
      ...(firstUserMessage ? [firstUserMessage] : []),
      ...state.plannerMessages,
    ]);

  return {
    plannerMessages: [response],
  };
}
