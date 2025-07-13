import {
  BaseMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";

// After 100k tokens, summarize the conversation history.
export const MAX_INTERNAL_TOKENS = 100_000;

export function calculateConversationHistoryTokenCount(
  messages: BaseMessage[],
) {
  let totalChars = 0;
  messages.forEach((m) => {
    if (isAIMessage(m)) {
      const contentString = getMessageContentString(m.content);
      totalChars += contentString.length;
      m.tool_calls?.forEach((tc) => {
        totalChars += tc.name.length;
        totalChars += JSON.stringify(tc.args).length;
      });
    }
    if (isHumanMessage(m) || isToolMessage(m)) {
      const contentString = getMessageContentString(m.content);
      totalChars += contentString.length;
    }
  });

  // Estimate 1 token for every 4 characters.
  return Math.ceil(totalChars / 4);
}

export function getMessagesSinceLastSummary(
  messages: BaseMessage[],
): BaseMessage[] {
  const allMessagesAfterLastSummary = messages.slice(
    messages.findIndex((m) => m.additional_kwargs?.summary_message),
  );
  return allMessagesAfterLastSummary;
}
