import {
  BaseMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";
import { traceable } from "langsmith/traceable";

// After 80k tokens, summarize the conversation history.
export const MAX_INTERNAL_TOKENS = 80_000;

export function calculateConversationHistoryTokenCount(
  messages: BaseMessage[],
  options?: {
    excludeHiddenMessages?: boolean;
    excludeCountFromEnd?: number;
  },
) {
  let totalTokens = 0;
  let messagesToCount = messages;

  if (options?.excludeCountFromEnd && options.excludeCountFromEnd > 0) {
    messagesToCount = getMessagesExcludingFromEnd(
      messages,
      options.excludeCountFromEnd,
    );
  }
  messagesToCount.forEach((m) => {
    if (options?.excludeHiddenMessages && m.additional_kwargs?.hidden) {
      return;
    }
    if (isHumanMessage(m) || isToolMessage(m)) {
      const contentString = getMessageContentString(m.content);
      // Divide each char by 4 as it's roughly one token per 4 characters.
      totalTokens += Math.ceil(contentString.length / 4);
    }

    if (isAIMessage(m)) {
      const usageMetadata = m.usage_metadata;
      if (usageMetadata) {
        totalTokens += usageMetadata.total_tokens;
      } else {
        const contentString = getMessageContentString(m.content);
        totalTokens += Math.ceil(contentString.length / 4);
        m.tool_calls?.forEach((tc) => {
          const nameAndArgs = tc.name + JSON.stringify(tc.args);
          totalTokens += Math.ceil(nameAndArgs.length / 4);
        });
      }
    }
  });

  return totalTokens;
}

/**
 * Helper function to exclude messages from the end while preserving AI/tool message pairs
 */
function getMessagesExcludingFromEnd(
  messages: BaseMessage[],
  excludeCount: number,
): BaseMessage[] {
  if (excludeCount <= 0 || excludeCount >= messages.length) {
    return excludeCount >= messages.length ? [] : messages;
  }

  let endIndex = messages.length - excludeCount;

  // Check if we're breaking up an AI message with tool calls and its corresponding tool messages
  // We need to look backwards from the cut point to ensure we don't separate AI/tool pairs
  while (endIndex > 0 && endIndex < messages.length) {
    const messageAtCutPoint = messages[endIndex - 1];

    // If the message before the cut point is an AI message with tool calls,
    // we need to check if there are corresponding tool messages after it
    if (
      isAIMessage(messageAtCutPoint) &&
      (messageAtCutPoint as any).tool_calls &&
      (messageAtCutPoint as any).tool_calls.length > 0
    ) {
      // Count how many tool messages follow this AI message
      let toolMessageCount = 0;
      for (
        let i = endIndex;
        i < messages.length && isToolMessage(messages[i]);
        i++
      ) {
        toolMessageCount++;
      }

      // If there are tool messages that would be cut off, move the cut point back
      // to include the AI message and all its tool messages, or exclude them entirely
      if (toolMessageCount > 0) {
        // Move cut point back to exclude the AI message entirely (safer approach)
        endIndex--;
        continue;
      }
    }

    // If the message at the cut point is a tool message, check if it belongs to an AI message
    if (isToolMessage(messages[endIndex])) {
      // Look backwards to find the corresponding AI message
      let aiMessageIndex = endIndex - 1;
      while (aiMessageIndex >= 0 && isToolMessage(messages[aiMessageIndex])) {
        aiMessageIndex--;
      }

      // If we found an AI message with tool calls, include all related messages
      if (
        aiMessageIndex >= 0 &&
        isAIMessage(messages[aiMessageIndex]) &&
        (messages[aiMessageIndex] as any).tool_calls &&
        (messages[aiMessageIndex] as any).tool_calls.length > 0
      ) {
        // Move cut point to include the entire AI/tool group
        let toolGroupEnd = endIndex;
        while (
          toolGroupEnd < messages.length &&
          isToolMessage(messages[toolGroupEnd])
        ) {
          toolGroupEnd++;
        }
        endIndex = toolGroupEnd;
        break;
      }
    }

    break;
  }

  return messages.slice(0, endIndex);
}

export function getMessagesSinceLastSummaryFunc(
  messages: BaseMessage[],
  options?: {
    excludeHiddenMessages?: boolean;
    excludeCountFromEnd?: number;
  },
): BaseMessage[] {
  // Find the last summary tool message (summary_messages are AI/tool pairs)
  const lastSummaryIndex = messages.findLastIndex(
    (m) => m.additional_kwargs?.summary_message && isToolMessage(m),
  );

  // Get all messages after the last summary_message
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
    messagesAfterLastSummary = getMessagesExcludingFromEnd(
      messagesAfterLastSummary,
      options.excludeCountFromEnd,
    );
  }

  return messagesAfterLastSummary;
}

export const getMessagesSinceLastSummary = traceable(
  getMessagesSinceLastSummaryFunc,
  {
    name: "get-messages-since-last-summary",
  },
);
