import { v4 as uuidv4 } from "uuid";
import { z } from "zod";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { loadModel, supportsParallelToolCallsParam, Task } from "../../../utils/load-model.js";
import { getMessageString } from "../../../utils/message/content.js";
import { getUserRequest } from "../../../utils/user-request.js";
import { formatCustomRulesPrompt } from "../../../utils/custom-rules.js";
import { getPlannerNotes } from "../utils/get-notes.js";
import { ToolMessage } from "@langchain/core/messages";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";
import { createWriteTechnicalNotesToolFields } from "@open-swe/shared/open-swe/tools";

const PLANNER_NOTES_PROMPT = `You've also taken technical notes throughout the context gathering process. Ensure you include/incorporate these notes, or the highest quality parts of these notes in your conclusion notes.

<planner_notes>
{PLANNER_NOTES}
</planner_notes>`;
const CUSTOM_RULES_EXTRA_CONTEXT =
  "- Carefully read over the user's custom rules to ensure you don't duplicate or repeat information found in that section, as you will always have access to it (even after the planning step!).";

const systemPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You've just finished gathering context to aid in generating a development plan to address the user's request. The context you've gathered is provided in the conversation history below.
After this, the conversation history will be deleted, and you'll start executing on the plan.
Your task is to carefully read over the conversation history, and take notes on the most important and useful actions you performed which will be helpful to you when you go and execute on the plan.
The notes you extract should be thoughtful, and should include technical details about the codebase, files, patterns, dependencies and setup instructions you discovered during the context gathering step, which you believe will be helpful when you go to execute on the plan.
These notes should not be overly verbose, as you'll be able to gather additional context when executing.
Your goal is to generate notes on all of the low-hanging fruit from the conversation history, to speed up the execution so that you don't need to duplicate work to gather context.

{CUSTOM_RULES}

{PLANNER_NOTES}

You MUST adhere to the following criteria when generating your notes:
- Do not retain any full code snippets.
- Do not retain any full file contents.
- Only take notes on the context provided below, and do not make up, or attempt to infer any information/context which is not explicitly provided.
- If mentioning specific code from the repo, ensure you also provide the path to the file the code is in.
- Carefully inspect the proposed plan. Your notes should be focused on context which will be most useful to you when you execute the plan. You may reference specific proposed plan items in your notes.
{EXTRA_RULES}

Here is the user's request
## User request:
{USER_REQUEST}

Here is the conversation history:
## Conversation history:
{CONVERSATION_HISTORY}

And here is the plan you just generated:
## Proposed plan:
{PROPOSED_PLAN}

With all of this in mind, please carefully inspect the conversation history, and the plan you generated. Then, determine which actions and context from the conversation history will be most useful to you when you execute the plan. After you're done analyzing, call the \`write_technical_notes\` tool.
`;

const formatPrompt = (state: PlannerGraphState): string => {
  const userRequest =
    getUserRequest(state.messages) || "No user request provided.";
  const plannerNotes = getPlannerNotes(state.messages)
    .map((n) => `  - ${n}`)
    .join("\n");

  return systemPrompt
    .replace("{USER_REQUEST}", userRequest)
    .replace(
      "{CONVERSATION_HISTORY}",
      state.messages.map(getMessageString).join("\n"),
    )
    .replace(
      "{PROPOSED_PLAN}",
      state.proposedPlan.map((p) => `  - ${p}`).join("\n"),
    )
    .replaceAll(
      "{CUSTOM_RULES}",
      formatCustomRulesPrompt(
        state.customRules,
        "Keep in mind these user provided rules will always be available to you, so any context present here should NOT be included in your notes as to not duplicate information.",
      ),
    )
    .replaceAll(
      "{PLANNER_NOTES}",
      plannerNotes.length
        ? PLANNER_NOTES_PROMPT.replace("{PLANNER_NOTES}", plannerNotes)
        : "",
    )
    .replaceAll(
      "{EXTRA_RULES}",
      state.customRules ? CUSTOM_RULES_EXTRA_CONTEXT : "",
    );
};

const condenseContextTool = createWriteTechnicalNotesToolFields();

export async function notetaker(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.SUMMARIZER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(config, Task.SUMMARIZER);
  const modelWithTools = model.bindTools([condenseContextTool], {
    tool_choice: condenseContextTool.name,
    ...(modelSupportsParallelToolCallsParam ? {
      parallel_tool_calls: false,
    } : {})
  });

  const conversationHistoryStr = `Here is the full conversation history:

${state.messages.map(getMessageString).join("\n")}`;

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(state),
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
  const toolResponse = new ToolMessage({
    id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
    tool_call_id: toolCall.id ?? "",
    content: "Successfully saved notes.",
    name: condenseContextTool.name,
  });

  return {
    messages: [response, toolResponse],
    contextGatheringNotes: (
      toolCall.args as z.infer<typeof condenseContextTool.schema>
    ).notes,
  };
}
