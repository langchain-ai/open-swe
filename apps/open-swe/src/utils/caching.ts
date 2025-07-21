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
import { CacheMetrics } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "Caching");

export interface CacheablePromptSegment {
  type: "text";
  text: string;
  cache_control?: { type: "ephemeral" };
}

function calculateCostSavings(metrics: CacheMetrics): number {
  const SONNET_4_BASE_RATE = 3.0 / 1_000_000; // $3 per MTok
  const CACHE_WRITE_MULTIPLIER = 1.25;
  const CACHE_READ_MULTIPLIER = 0.1;

  const cacheWriteCost =
    metrics.cacheCreationInputTokens *
    SONNET_4_BASE_RATE *
    CACHE_WRITE_MULTIPLIER;

  const cacheReadCost =
    metrics.cacheReadInputTokens * SONNET_4_BASE_RATE * CACHE_READ_MULTIPLIER;

  const regularInputCost = metrics.inputTokens * SONNET_4_BASE_RATE;

  // Cost without caching (all tokens at base rate)
  const totalTokens =
    metrics.cacheCreationInputTokens +
    metrics.cacheReadInputTokens +
    metrics.inputTokens;
  const costWithoutCaching = totalTokens * SONNET_4_BASE_RATE;

  // Actual cost with caching
  const actualCost = cacheWriteCost + cacheReadCost + regularInputCost;

  return costWithoutCaching - actualCost;
}

export function trackCachePerformance(response: AIMessageChunk): CacheMetrics {
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
  const costSavings = calculateCostSavings(metrics);

  logger.info("Cache Performance", {
    cacheHitRate: `${(cacheHitRate * 100).toFixed(2)}%`,
    costSavings: `$${costSavings.toFixed(4)}`,
    ...metrics,
  });

  return metrics;
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

export function convertToCacheControlMessage(
  message: BaseMessage,
): BaseMessage {
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
