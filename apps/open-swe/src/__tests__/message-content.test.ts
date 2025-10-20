import { describe, expect, test } from "@jest/globals";
import { AIMessage, ToolMessage } from "@langchain/core/messages";

import { filterMessagesWithoutContent } from "../utils/message/content.js";

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
