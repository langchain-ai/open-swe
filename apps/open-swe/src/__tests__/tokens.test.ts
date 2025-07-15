import { describe, it, expect } from "@jest/globals";
import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import {
  calculateConversationHistoryTokenCount,
  getMessagesSinceLastSummary,
  MAX_INTERNAL_TOKENS,
} from "../utils/tokens.js";

describe("calculateConversationHistoryTokenCount", () => {
  it("should return 0 for empty messages array", () => {
    const result = calculateConversationHistoryTokenCount([]);
    expect(result).toBe(0);
  });

  it("should calculate token count for human messages", () => {
    const messages = [
      new HumanMessage({
        content: "This is a test message with exactly 10 words in it.",
      }),
    ];

    // 10 words, approximately 13 tokens, ~52 characters
    // Since we estimate 1 token per 4 characters, this should be around 13 tokens
    const result = calculateConversationHistoryTokenCount(messages);
    expect(result).toBe(13);
  });

  it("should calculate token count for AI messages with usage metadata", () => {
    const messages = [
      new AIMessage({
        content: "AI response",
        usage_metadata: {
          input_tokens: 10,
          output_tokens: 10,
          total_tokens: 20,
        },
      }),
    ];

    const result = calculateConversationHistoryTokenCount(messages);
    expect(result).toBe(20);
  });

  it("should calculate token count for AI messages without usage metadata", () => {
    const messages = [
      new AIMessage({
        content: "This is an AI response with no usage metadata.",
      }),
    ];

    // ~12 words, approximately 12 tokens, ~48 characters
    // Since we estimate 1 token per 4 characters, this should be around 12 tokens
    const result = calculateConversationHistoryTokenCount(messages);
    expect(result).toBe(12);
  });

  it("should calculate token count for AI messages with tool calls", () => {
    const messages = [
      new AIMessage({
        content: "Using a tool",
        tool_calls: [
          {
            name: "calculator",
            args: { a: 1, b: 2 },
          },
        ],
      }),
    ];

    // Content: "Using a tool" (~3 tokens)
    // Tool name: "calculator" (~2 tokens)
    // Args: JSON.stringify({a:1,b:2}) (~3 tokens)
    // Total: ~8 tokens
    const result = calculateConversationHistoryTokenCount(messages);
    expect(result).toBeGreaterThan(0);
  });

  it("should calculate token count for tool messages", () => {
    const messages = [
      new ToolMessage({
        content: "Result of tool execution with some data.",
        tool_call_id: "tool-1",
        name: "tool",
      }),
    ];

    // ~8 words, approximately 10 tokens, ~40 characters
    const result = calculateConversationHistoryTokenCount(messages);
    expect(result).toBe(10);
  });

  it("should exclude hidden messages when option is provided", () => {
    const messages = [
      new HumanMessage({
        content: "Visible message",
      }),
      new HumanMessage({
        content: "Hidden message",
        additional_kwargs: { hidden: true },
      }),
    ];

    const resultWithoutOption =
      calculateConversationHistoryTokenCount(messages);
    const resultWithOption = calculateConversationHistoryTokenCount(messages, {
      excludeHiddenMessages: true,
    });

    expect(resultWithoutOption).toBeGreaterThan(resultWithOption);
    expect(resultWithOption).toBe(4); // "Visible message" is ~4 tokens
  });

  it("should exclude messages from the end when option is provided", () => {
    const messages = [
      new HumanMessage({ content: "First message" }),
      new HumanMessage({ content: "Second message" }),
      new HumanMessage({ content: "Third message" }),
    ];

    const resultWithoutOption =
      calculateConversationHistoryTokenCount(messages);
    const resultWithOption = calculateConversationHistoryTokenCount(messages, {
      excludeCountFromEnd: 1,
    });

    expect(resultWithoutOption).toBeGreaterThan(resultWithOption);
    // First two messages should be ~7 tokens
    expect(resultWithOption).toBe(7);
  });
});

describe("getMessagesSinceLastSummary", () => {
  it("should return all messages when there is no summary message", () => {
    const messages = [
      new HumanMessage({ content: "Message 1" }),
      new AIMessage({ content: "Message 2" }),
      new HumanMessage({ content: "Message 3" }),
    ];

    const result = getMessagesSinceLastSummary(messages);
    expect(result).toHaveLength(3);
    expect(result).toEqual(messages);
  });

  it("should return messages after the last summary message", () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      new HumanMessage({ content: "Message 1" }),
      summaryMessage,
      new HumanMessage({ content: "Message 3" }),
      new AIMessage({ content: "Message 4" }),
    ];

    const result = getMessagesSinceLastSummary(messages);
    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Message 3");
    expect(result[1].content).toBe("Message 4");
  });

  it("should exclude hidden messages when option is provided", () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      summaryMessage,
      new HumanMessage({ content: "Visible message" }),
      new HumanMessage({
        content: "Hidden message",
        additional_kwargs: { hidden: true },
      }),
      new AIMessage({ content: "Another visible message" }),
    ];

    const result = getMessagesSinceLastSummary(messages, {
      excludeHiddenMessages: true,
    });

    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Visible message");
    expect(result[1].content).toBe("Another visible message");
  });

  it("should exclude messages from the end when option is provided", () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      summaryMessage,
      new HumanMessage({ content: "Message 1" }),
      new AIMessage({ content: "Message 2" }),
      new HumanMessage({ content: "Message 3" }),
    ];

    const result = getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 1,
    });

    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Message 1");
    expect(result[1].content).toBe("Message 2");
  });

  it("should handle both excludeHiddenMessages and excludeCountFromEnd options", () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      summaryMessage,
      new HumanMessage({ content: "Message 1" }),
      new HumanMessage({
        content: "Hidden message",
        additional_kwargs: { hidden: true },
      }),
      new AIMessage({ content: "Message 3" }),
      new HumanMessage({ content: "Message 4" }),
    ];

    const result = getMessagesSinceLastSummary(messages, {
      excludeHiddenMessages: true,
      excludeCountFromEnd: 1,
    });

    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Message 1");
    expect(result[1].content).toBe("Message 3");
  });

  it("should return empty array if all messages are before the summary", () => {
    const messages = [
      new HumanMessage({ content: "Message 1" }),
      new AIMessage({ content: "Message 2" }),
      new AIMessage({
        content: "Summary of conversation",
        additional_kwargs: { summary_message: true },
      }),
    ];

    const result = getMessagesSinceLastSummary(messages);
    expect(result).toHaveLength(0);
  });
});

describe("MAX_INTERNAL_TOKENS constant", () => {
  it("should be defined as 60,000", () => {
    expect(MAX_INTERNAL_TOKENS).toBe(60_000);
  });
});
