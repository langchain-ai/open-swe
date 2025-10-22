import { describe, expect, it } from "vitest";
import type { AIMessage, HumanMessage, ToolMessage } from "@langchain/langgraph-sdk";

import {
  isAIMessageSDK,
  isHumanMessageSDK,
  isToolMessageSDK,
} from "./langchain-messages";

describe("langchain message type guards", () => {
  const aiMessage: AIMessage = {
    type: "ai",
    content: "Hello from AI",
  };

  const humanMessage: HumanMessage = {
    type: "human",
    content: "Hello from human",
  };

  const toolMessage: ToolMessage = {
    type: "tool",
    content: "Tool output",
    tool_call_id: "call-1",
  };

  it("returns false for undefined inputs", () => {
    expect(isAIMessageSDK(undefined)).toBe(false);
    expect(isHumanMessageSDK(undefined)).toBe(false);
    expect(isToolMessageSDK(undefined)).toBe(false);
  });

  it("returns false for null inputs", () => {
    expect(isAIMessageSDK(null)).toBe(false);
    expect(isHumanMessageSDK(null)).toBe(false);
    expect(isToolMessageSDK(null)).toBe(false);
  });

  it("preserves type guard behavior for valid messages", () => {
    expect(isAIMessageSDK(aiMessage)).toBe(true);
    expect(isHumanMessageSDK(humanMessage)).toBe(true);
    expect(isToolMessageSDK(toolMessage)).toBe(true);
  });
});
