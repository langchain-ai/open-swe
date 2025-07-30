import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { describe, it, expect } from "@jest/globals";
import {
  AIMessage,
  coerceMessageLikeToMessage,
  HumanMessage,
  ToolMessage,
} from "@langchain/core/messages";
import {
  calculateConversationHistoryTokenCount,
  getMessagesSinceLastSummary,
} from "../utils/tokens.js";
import { GraphState } from "@open-swe/shared/open-swe/types";

describe("calculateConversationHistoryTokenCount", () => {
  it("should return 0 for empty messages array", async () => {
    const result = calculateConversationHistoryTokenCount([]);
    expect(result).toBe(0);
  });

  it("should calculate token count for human messages", async () => {
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

  it("should calculate token count for AI messages with usage metadata", async () => {
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

  it("should calculate token count for AI messages without usage metadata", async () => {
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

  it("should calculate token count for AI messages with tool calls", async () => {
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

  it("should calculate token count for tool messages", async () => {
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

  it("should exclude hidden messages when option is provided", async () => {
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

  it("should exclude messages from the end when option is provided", async () => {
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
    // First two messages should be ~8 tokens
    expect(resultWithOption).toBe(8);
  });

  it("should not separate AI messages with tool calls from their tool messages when excluding from end", async () => {
    const aiMessageWithToolCalls = new AIMessage({
      content: "I'll help you with that",
      tool_calls: [
        {
          name: "test_tool",
          args: { param: "value" },
          id: "call_123",
        },
      ],
    });

    const toolMessage = new ToolMessage({
      content: "Tool result",
      tool_call_id: "call_123",
    });

    const messages = [
      new HumanMessage({ content: "First message" }),
      aiMessageWithToolCalls,
      toolMessage,
      new HumanMessage({ content: "Last message" }),
    ];

    // Try to exclude 2 messages from the end, which would normally cut between AI and tool message
    const result = calculateConversationHistoryTokenCount(messages, {
      excludeCountFromEnd: 2,
    });

    // Should only count the first human message since we can't separate AI/tool pair
    const expectedResult = calculateConversationHistoryTokenCount([
      new HumanMessage({ content: "First message" }),
    ]);

    expect(result).toBe(expectedResult);
  });

  it("should preserve multiple tool messages following an AI message", async () => {
    const aiMessageWithToolCalls = new AIMessage({
      content: "I'll use multiple tools",
      tool_calls: [
        {
          name: "tool1",
          args: { param: "value1" },
          id: "call_1",
        },
        {
          name: "tool2",
          args: { param: "value2" },
          id: "call_2",
        },
      ],
    });

    const toolMessage1 = new ToolMessage({
      content: "Tool 1 result",
      tool_call_id: "call_1",
    });

    const toolMessage2 = new ToolMessage({
      content: "Tool 2 result",
      tool_call_id: "call_2",
    });

    const messages = [
      new HumanMessage({ content: "First message" }),
      aiMessageWithToolCalls,
      toolMessage1,
      toolMessage2,
      new HumanMessage({ content: "Last message" }),
    ];

    // Try to exclude 3 messages from the end, which would cut in the middle of tool messages
    const result = calculateConversationHistoryTokenCount(messages, {
      excludeCountFromEnd: 3,
    });

    // Should only count the first human message
    const expectedResult = calculateConversationHistoryTokenCount([
      new HumanMessage({ content: "First message" }),
    ]);

    expect(result).toBe(expectedResult);
  });
});

describe("getMessagesSinceLastSummary", () => {
  it("should return all messages when there is no summary message", async () => {
    const messages = [
      new HumanMessage({ content: "Message 1" }),
      new AIMessage({ content: "Message 2" }),
      new HumanMessage({ content: "Message 3" }),
    ];

    const result = await getMessagesSinceLastSummary(messages);
    expect(result).toHaveLength(3);
    expect(result).toEqual(messages);
  });

  it("should return messages after the last summary message", async () => {
    const summaryAIMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      new HumanMessage({ content: "Message 1" }),
      summaryAIMessage,
      summaryToolMessage,
      new HumanMessage({ content: "Message 3" }),
      new AIMessage({ content: "Message 4" }),
    ];

    const result = await getMessagesSinceLastSummary(messages);
    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Message 3");
    expect(result[1].content).toBe("Message 4");
  });

  it("should return messages after the last summary message, when there are multiple", async () => {
    const summaryAIMessage1 = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage1 = new ToolMessage({
      tool_call_id: "tool-call-id-1",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const summaryAIMessage2 = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage2 = new ToolMessage({
      tool_call_id: "tool-call-id-1",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      new HumanMessage({ content: "Message 1" }),
      summaryAIMessage1,
      summaryToolMessage1,
      new HumanMessage({ content: "Message 4" }),
      new AIMessage({ content: "Message 5" }),
      summaryAIMessage2,
      summaryToolMessage2,
      new HumanMessage({ content: "Message 8" }),
      new AIMessage({ content: "Message 9" }),
    ];

    const result = await getMessagesSinceLastSummary(messages);
    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Message 8");
    expect(result[1].content).toBe("Message 9");
  });

  it("should exclude hidden messages when option is provided", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "Visible message" }),
      new HumanMessage({
        content: "Hidden message",
        additional_kwargs: { hidden: true },
      }),
      new AIMessage({ content: "Another visible message" }),
    ];

    const result = await getMessagesSinceLastSummary(messages, {
      excludeHiddenMessages: true,
    });

    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Visible message");
    expect(result[1].content).toBe("Another visible message");
  });

  it("should exclude messages from the end when option is provided", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "Message 1" }),
      new AIMessage({ content: "Message 2" }),
      new HumanMessage({ content: "Message 3" }),
    ];

    const result = await getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 1,
    });

    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Message 1");
    expect(result[1].content).toBe("Message 2");
  });

  it("should handle both excludeHiddenMessages and excludeCountFromEnd options", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "Message 1" }),
      new HumanMessage({
        content: "Hidden message",
        additional_kwargs: { hidden: true },
      }),
      new AIMessage({ content: "Message 3" }),
      new HumanMessage({ content: "Message 4" }),
    ];

    const result = await getMessagesSinceLastSummary(messages, {
      excludeHiddenMessages: true,
      excludeCountFromEnd: 1,
    });

    expect(result).toHaveLength(2);
    expect(result[0].content).toBe("Message 1");
    expect(result[1].content).toBe("Message 3");
  });

  it("should not separate AI messages with tool calls from their tool messages when excluding from end", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const aiMessageWithToolCalls = new AIMessage({
      content: "I'll help you with that",
      tool_calls: [
        {
          name: "test_tool",
          args: { param: "value" },
          id: "call_123",
        },
      ],
    });

    const toolMessage = new ToolMessage({
      content: "Tool result",
      tool_call_id: "call_123",
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "First message" }),
      aiMessageWithToolCalls,
      toolMessage,
      new HumanMessage({ content: "Last message" }),
    ];

    // Try to exclude 2 messages from the end, which would normally cut between AI and tool message
    const result = await getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 2,
    });

    // Should only include the first human message since we can't separate AI/tool pair
    expect(result).toHaveLength(1);
    expect(result[0].content).toBe("First message");
  });

  it("should preserve multiple tool messages following an AI message in getMessagesSinceLastSummary", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const aiMessageWithToolCalls = new AIMessage({
      content: "I'll use multiple tools",
      tool_calls: [
        {
          name: "tool1",
          args: { param: "value1" },
          id: "call_1",
        },
        {
          name: "tool2",
          args: { param: "value2" },
          id: "call_2",
        },
      ],
    });

    const toolMessage1 = new ToolMessage({
      content: "Tool 1 result",
      tool_call_id: "call_1",
    });

    const toolMessage2 = new ToolMessage({
      content: "Tool 2 result",
      tool_call_id: "call_2",
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "First message" }),
      aiMessageWithToolCalls,
      toolMessage1,
      toolMessage2,
      new HumanMessage({ content: "Last message" }),
    ];

    // Try to exclude 3 messages from the end, which would cut in the middle of tool messages
    const result = await getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 3,
    });

    // Should only include the first human message
    expect(result).toHaveLength(1);
    expect(result[0].content).toBe("First message");
  });

  it("should exclude entire AI/tool group when cut point would separate them", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const aiMessageWithToolCalls = new AIMessage({
      content: "I'll use a tool",
      tool_calls: [
        {
          name: "test_tool",
          args: { param: "value" },
          id: "call_123",
        },
      ],
    });

    const toolMessage = new ToolMessage({
      content: "Tool result",
      tool_call_id: "call_123",
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "First message" }),
      aiMessageWithToolCalls,
      toolMessage,
    ];

    // Try to exclude 1 message from the end (just the tool message)
    const result = await getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 1,
    });

    // Should exclude the entire AI/tool group to maintain integrity
    expect(result).toHaveLength(1);
    expect(result[0].content).toBe("First message");
  });

  it("should preserve AI message with multiple tool calls and their corresponding tool messages", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const aiMessageWithMultipleToolCalls = new AIMessage({
      content: "I'll use multiple tools to help you",
      tool_calls: [
        {
          name: "search_tool",
          args: { query: "example query" },
          id: "call_search_123",
        },
        {
          name: "calculator_tool",
          args: { expression: "2 + 2" },
          id: "call_calc_456",
        },
        {
          name: "file_tool",
          args: { filename: "test.txt" },
          id: "call_file_789",
        },
      ],
    });

    const searchToolMessage = new ToolMessage({
      content: "Search results found",
      tool_call_id: "call_search_123",
    });

    const calculatorToolMessage = new ToolMessage({
      content: "Result: 4",
      tool_call_id: "call_calc_456",
    });

    const fileToolMessage = new ToolMessage({
      content: "File contents: Hello world",
      tool_call_id: "call_file_789",
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "First message" }),
      aiMessageWithMultipleToolCalls,
      searchToolMessage,
      calculatorToolMessage,
      fileToolMessage,
      new HumanMessage({ content: "After all tools" }),
      new HumanMessage({ content: "Last message" }),
    ];

    // Try to exclude 4 messages from the end, which would cut in the middle of the tool messages
    const result = await getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 4,
    });

    // Should include the first human message and the complete AI/tool group since we can't separate them
    expect(result).toHaveLength(5);
    expect(result[0].content).toBe("First message");
    expect(result[1].content).toBe("I'll use multiple tools to help you");
    expect(result[2].content).toBe("Search results found");
    expect(result[3].content).toBe("Result: 4");
    expect(result[4].content).toBe("File contents: Hello world");
  });

  it("should include complete AI/tool group when exclusion doesn't break the group", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const aiMessageWithMultipleToolCalls = new AIMessage({
      content: "I'll use two tools",
      tool_calls: [
        {
          name: "tool1",
          args: { param: "value1" },
          id: "call_1",
        },
        {
          name: "tool2",
          args: { param: "value2" },
          id: "call_2",
        },
      ],
    });

    const tool1Message = new ToolMessage({
      content: "Tool 1 result",
      tool_call_id: "call_1",
    });

    const tool2Message = new ToolMessage({
      content: "Tool 2 result",
      tool_call_id: "call_2",
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "First message" }),
      aiMessageWithMultipleToolCalls,
      tool1Message,
      tool2Message,
      new HumanMessage({ content: "After tools" }),
      new HumanMessage({ content: "Second to last" }),
      new HumanMessage({ content: "Last message" }),
    ];

    // Try to exclude 2 messages from the end (just the last two human messages)
    const result = await getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 2,
    });

    // Should include the first human message, the AI message, both tool messages, and the "After tools" message
    expect(result).toHaveLength(5);
    expect(result[0].content).toBe("First message");
    expect(result[1].content).toBe("I'll use two tools");
    expect(result[2].content).toBe("Tool 1 result");
    expect(result[3].content).toBe("Tool 2 result");
    expect(result[4].content).toBe("After tools");
  });

  it("should handle case where AI/tool group can be included entirely", async () => {
    const summaryMessage = new AIMessage({
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });
    const summaryToolMessage = new ToolMessage({
      tool_call_id: "tool-call-id",
      content: "Summary of conversation",
      additional_kwargs: { summary_message: true },
    });

    const aiMessageWithToolCalls = new AIMessage({
      content: "I'll use a tool",
      tool_calls: [
        {
          name: "test_tool",
          args: { param: "value" },
          id: "call_123",
        },
      ],
    });

    const toolMessage = new ToolMessage({
      content: "Tool result",
      tool_call_id: "call_123",
    });

    const messages = [
      summaryMessage,
      summaryToolMessage,
      new HumanMessage({ content: "First message" }),
      aiMessageWithToolCalls,
      toolMessage,
      new HumanMessage({ content: "After tool message" }),
      new HumanMessage({ content: "Last message" }),
    ];

    // Try to exclude 2 messages from the end (the last two human messages)
    const result = await getMessagesSinceLastSummary(messages, {
      excludeCountFromEnd: 2,
    });

    // Should include the first human message and the complete AI/tool group
    expect(result).toHaveLength(3);
    expect(result[0].content).toBe("First message");
    expect(result[1].content).toBe("I'll use a tool");
    expect(result[2].content).toBe("Tool result");
  });

  it("should return empty array if all messages are before the summary", async () => {
    const messages = [
      new HumanMessage({ content: "Message 1" }),
      new AIMessage({ content: "Message 2" }),
      new AIMessage({
        content: "Summary of conversation",
        additional_kwargs: { summary_message: true },
      }),
      new ToolMessage({
        tool_call_id: "tool-call-id",
        content: "Summary of conversation",
        additional_kwargs: { summary_message: true },
      }),
    ];

    const result = await getMessagesSinceLastSummary(messages);
    expect(result).toHaveLength(0);
  });

  it("retains the last summary tool messages from a real trace", async () => {
    const __dirname = path.dirname(fileURLToPath(import.meta.url));
    const basePath = path.join(__dirname, "data");
    const inputs: GraphState = JSON.parse(
      fs.readFileSync(
        path.join(basePath, "summarize-history-input.json"),
        "utf-8",
      ),
    );

    const conversationHistoryToSummarize = await getMessagesSinceLastSummary(
      inputs.internalMessages.map(coerceMessageLikeToMessage),
      {
        excludeHiddenMessages: true,
        excludeCountFromEnd: 20,
      },
    );

    const expectedToolMessageId = "465097e3-3c65-4af1-beb5-c3d9444219fd";
    const toolMessageExists = conversationHistoryToSummarize.find(
      (m) => m.id === expectedToolMessageId,
    );
    expect(toolMessageExists).not.toBeDefined();
  });
});
