import {
  BaseMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";

// After 60k tokens, summarize the conversation history.
export const MAX_INTERNAL_TOKENS = 60_000;

export function calculateConversationHistoryTokenCount(
  messages: BaseMessage[],
  options?: {
    excludeHiddenMessages?: boolean;
    excludeCountFromEnd?: number;
  },
) {
  let totalChars = 0;
  const messagesToCount = options?.excludeCountFromEnd
    ? messages.slice(0, -options.excludeCountFromEnd)
    : messages;
  messagesToCount.forEach((m) => {
    if (options?.excludeHiddenMessages && m.additional_kwargs?.hidden) {
      return;
    }
    if (isHumanMessage(m) || isToolMessage(m)) {
      const contentString = getMessageContentString(m.content);
      totalChars += contentString.length;
    }

    if (isAIMessage(m)) {
      const usageMetadata = m.usage_metadata;
      if (usageMetadata) {
        // multiply by 4 here since we divide by 4 to estimate tokens.
        totalChars += usageMetadata.total_tokens * 4;
      } else {
        const contentString = getMessageContentString(m.content);
        totalChars += contentString.length;
        m.tool_calls?.forEach((tc) => {
          totalChars += tc.name.length;
          totalChars += JSON.stringify(tc.args).length;
        });
      }
    }
  });

  // Estimate 1 token for every 4 characters.
  return Math.ceil(totalChars / 4);
}

export function getMessagesSinceLastSummary(
  messages: BaseMessage[],
  options?: {
    excludeHiddenMessages?: boolean;
    excludeCountFromEnd?: number;
  },
): BaseMessage[] {
  // Find the index of the last summary message
  const lastSummaryIndex = messages.findIndex(
    (m) => m.additional_kwargs?.summary_message,
  );

  // Get all messages after the last summary message
  let messagesAfterLastSummary =
    lastSummaryIndex >= 0
      ? messages.slice(lastSummaryIndex + 1)
      : [...messages];

  // Apply excludeHiddenMessages option if provided
  if (options?.excludeHiddenMessages) {
    messagesAfterLastSummary = messagesAfterLastSummary.filter(
      (m) => !m.additional_kwargs?.hidden,
    );
  }

  // Apply excludeCountFromEnd option if provided
  if (options?.excludeCountFromEnd && options.excludeCountFromEnd > 0) {
    messagesAfterLastSummary = messagesAfterLastSummary.slice(
      0,
      Math.max(
        0,
        messagesAfterLastSummary.length - options.excludeCountFromEnd,
      ),
    );
  }

  return messagesAfterLastSummary;
}
