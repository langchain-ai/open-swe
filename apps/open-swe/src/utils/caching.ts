import {
  AIMessage,
  AIMessageChunk,
  BaseMessage,
  HumanMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
  MessageContent,
  ToolMessage,
} from "@langchain/core/messages";
import { CacheMetrics, ModelTokenData } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";
import { calculateCostSavings } from "@open-swe/shared/caching";

const logger = createLogger(LogLevel.INFO, "Caching");

export interface CacheablePromptSegment {
  type: "text";
  text: string;
  cache_control?: { type: "ephemeral" };
}

export function trackCachePerformance(
  response: AIMessageChunk,
  model: string,
): ModelTokenData[] {
  const metrics: CacheMetrics = {
    cacheCreationInputTokens:
      response.usage_metadata?.input_token_details?.cache_creation || 0,
    cacheReadInputTokens:
      response.usage_metadata?.input_token_details?.cache_read || 0,
    inputTokens: response.usage_metadata?.input_tokens || 0,
    outputTokens: response.usage_metadata?.output_tokens || 0,
  };

  const totalInputTokens =
    metrics.cacheCreationInputTokens +
    metrics.cacheReadInputTokens +
    metrics.inputTokens;

  const cacheHitRate =
    totalInputTokens > 0 ? metrics.cacheReadInputTokens / totalInputTokens : 0;
  const costSavings = calculateCostSavings(metrics).totalSavings;

  logger.info("Cache Performance", {
    model,
    cacheHitRate: `${(cacheHitRate * 100).toFixed(2)}%`,
    costSavings: `$${costSavings.toFixed(4)}`,
    ...metrics,
  });

  return [
    {
      ...metrics,
      model,
    },
  ];
}

function addCacheControlToMessageContent(
  messageContent: MessageContent,
): MessageContent {
  if (typeof messageContent === "string") {
    return [
      {
        type: "text",
        text: messageContent,
        cache_control: { type: "ephemeral" },
      },
    ];
  } else if (Array.isArray(messageContent)) {
    if ("cache_control" in messageContent[messageContent.length - 1]) {
      // Already set, no-op
      return messageContent;
    }

    const newMessageContent = [...messageContent];
    newMessageContent[newMessageContent.length - 1] = {
      ...newMessageContent[newMessageContent.length - 1],
      cache_control: { type: "ephemeral" },
    };
    return newMessageContent;
  } else {
    logger.warn("Unknown message content type", { messageContent });
    return messageContent;
  }
}

function convertToCacheControlMessage(message: BaseMessage): BaseMessage {
  if (isAIMessage(message)) {
    return new AIMessage({
      ...message,
      content: addCacheControlToMessageContent(message.content),
    });
  } else if (isHumanMessage(message)) {
    return new HumanMessage({
      ...message,
      content: addCacheControlToMessageContent(message.content),
    });
  } else if (isToolMessage(message)) {
    return new ToolMessage({
      ...(message as ToolMessage),
      content: addCacheControlToMessageContent(
        (message as ToolMessage).content,
      ),
    });
  } else {
    return message;
  }
}

export function convertMessagesToCacheControlledMessages(
  messages: BaseMessage[],
) {
  if (messages.length === 0) {
    return messages;
  }

  const newMessages = [...messages];
  const lastIndex = newMessages.length - 1;
  newMessages[lastIndex] = convertToCacheControlMessage(newMessages[lastIndex]);
  return newMessages;
}
