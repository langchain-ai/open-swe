import { describe, expect, test } from "@jest/globals";
import { AIMessage, ToolMessage } from "@langchain/core/messages";

import {
  filterMessagesWithoutContent,
  sanitizeMessagesWithToolCallResponses,
} from "../utils/message/content.js";

describe("filterMessagesWithoutContent", () => {
  test("keeps tool messages linked to tool calls even without text content", () => {
    const toolCallId = "test-tool-call";
    const messages = [
      new AIMessage({
        content: "",
        tool_calls: [
          {
            id: toolCallId,
            name: "test_tool",
            args: { input: "value" },
            type: "tool_call",
          },
        ],
      }),
      new ToolMessage({
        tool_call_id: toolCallId,
        content: "",
        name: "test_tool",
      }),
    ];

    const filtered = filterMessagesWithoutContent(messages);

    expect(filtered).toHaveLength(2);
    const toolMessage = filtered[1];
    expect(toolMessage).toBeInstanceOf(ToolMessage);
    expect((toolMessage as ToolMessage).tool_call_id).toBe(toolCallId);
  });
});

describe("sanitizeMessagesWithToolCallResponses", () => {
  test("removes tool calls without matching tool responses", () => {
    const toolCallId = "missing-tool-response";
    const messages = [
      new AIMessage({
        content: "",
        tool_calls: [
          {
            id: toolCallId,
            name: "test_tool",
            args: {},
            type: "tool_call",
          },
        ],
      }),
    ];

    const sanitized = sanitizeMessagesWithToolCallResponses(messages);

    expect(sanitized).toHaveLength(0);
  });

  test("keeps message content but strips incomplete tool calls", () => {
    const toolCallId = "unanswered-call";
    const messages = [
      new AIMessage({
        content: "Some content to keep",
        tool_calls: [
          {
            id: toolCallId,
            name: "test_tool",
            args: {},
            type: "tool_call",
          },
        ],
      }),
      new ToolMessage({
        name: "test_tool",
        tool_call_id: "different-call",
        content: "Should be removed",
      }),
    ];

    const sanitized = sanitizeMessagesWithToolCallResponses(messages);

    expect(sanitized).toHaveLength(1);
    const aiMessage = sanitized[0] as AIMessage;
    expect(aiMessage.content).toBe("Some content to keep");
    expect(aiMessage.tool_calls).toHaveLength(0);
  });

  test("retains complete tool call and response pairs", () => {
    const toolCallId = "complete-call";
    const messages = [
      new AIMessage({
        content: "",
        tool_calls: [
          {
            id: toolCallId,
            name: "test_tool",
            args: {},
            type: "tool_call",
          },
        ],
      }),
      new ToolMessage({
        name: "test_tool",
        tool_call_id: toolCallId,
        content: "result",
      }),
    ];

    const sanitized = sanitizeMessagesWithToolCallResponses(messages);

    expect(sanitized).toHaveLength(2);
    const aiMessage = sanitized[0] as AIMessage;
    expect(aiMessage.tool_calls).toHaveLength(1);
    const toolMessage = sanitized[1] as ToolMessage;
    expect(toolMessage.tool_call_id).toBe(toolCallId);
  });
});
