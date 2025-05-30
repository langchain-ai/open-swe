import { BaseMessage, isHumanMessage } from "@langchain/core/messages";
import { PlanItem } from "../../../types.js";
import { getMessageString } from "../../../utils/message/content.js";
import { formatPlanPromptWithSummaries } from "../../../utils/plan-prompt.js";

const followupMessagePrompt = `
The user is sending a followup request, so you should assume the most recent message they sent is their request. You are also provided with the full conversation history from their previous request, which you should use as context when generating a plan.

Here is the full list of tasks and task summaries from the previous plan you executed:
{PREVIOUS_PLAN}

Here is the full conversation history from the previous plan you executed:
{PREVIOUS_CONVERSATION_HISTORY}
`;

export function formatFollowupMessagePrompt(
  plan: PlanItem[],
  conversationHistory: BaseMessage[],
): string {
  const lastHumanMessageIndex =
    conversationHistory.findLastIndex(isHumanMessage);
  const messagesWithoutLastHumanMessage = conversationHistory.slice(
    0,
    lastHumanMessageIndex,
  );
  return followupMessagePrompt
    .replace("{PREVIOUS_PLAN}", formatPlanPromptWithSummaries(plan))
    .replace(
      "{PREVIOUS_CONVERSATION_HISTORY}",
      messagesWithoutLastHumanMessage.map(getMessageString).join("\n"),
    );
}
