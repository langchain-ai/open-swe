import {
  BaseMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";

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
