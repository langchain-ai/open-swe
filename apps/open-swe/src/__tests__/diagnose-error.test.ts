import { describe, it, expect } from "@jest/globals";
import { AIMessage, ToolMessage, HumanMessage } from "@langchain/core/messages";
import { getAllLastFailedActions } from "../utils/tool-message-error.js";

describe("getAllLastFailedActions", () => {
  it("should return empty string for empty messages array", () => {
    const result = getAllLastFailedActions([]);
    expect(result).toBe("");
  });

  it("should return AI and error tool message pairs until a non-error tool message is encountered", () => {
    // Create test messages
    const aiMessage1 = new AIMessage({
      content: "I'll try to execute this command",
      id: "ai-1",
    });

    const errorToolMessage1 = new ToolMessage({
      content: "Command failed: Permission denied",
      tool_call_id: "tool-1",
      name: "shell",
      status: "error",
    });

    const aiMessage2 = new AIMessage({
      content: "Let me try a different approach",
      id: "ai-2",
    });

    const errorToolMessage2 = new ToolMessage({
      content: "Error: File not found",
      tool_call_id: "tool-2",
      name: "read_file",
      status: "error",
    });

    const aiMessage3 = new AIMessage({
      content: "Let me try something else",
      id: "ai-3",
    });

    const successToolMessage = new ToolMessage({
      content: "Command executed successfully",
      tool_call_id: "tool-3",
      name: "shell",
      status: "success",
    });

    const aiMessage4 = new AIMessage({
      content: "Let me try one more thing",
      id: "ai-4",
    });

    const errorToolMessage3 = new ToolMessage({
      content: "Error: Invalid syntax",
      tool_call_id: "tool-4",
      name: "shell",
      status: "error",
    });

    const messages = [
      aiMessage1,
      errorToolMessage1,
      aiMessage2,
      errorToolMessage2,
      aiMessage3,
      successToolMessage,
      aiMessage4,
      errorToolMessage3,
    ];

    const result = getAllLastFailedActions(messages);

    // Should include the first two AI+error pairs, but stop at the success message
    expect(result).toContain("I'll try to execute this command");
    expect(result).toContain("Command failed: Permission denied");
    expect(result).toContain("Let me try a different approach");
    expect(result).toContain("Error: File not found");

    // Should not include messages after the success message
    expect(result).not.toContain("Let me try one more thing");
    expect(result).not.toContain("Error: Invalid syntax");
  });

  it("should handle non-sequential AI and tool messages", () => {
    const aiMessage = new AIMessage({
      content: "I'll try to execute this command",
      id: "ai-1",
    });

    const humanMessage = new HumanMessage({
      content: "Can you try something else?",
      id: "human-1",
    });

    const errorToolMessage = new ToolMessage({
      content: "Command failed: Permission denied",
      tool_call_id: "tool-1",
      name: "shell",
      status: "error",
    });

    const messages = [aiMessage, humanMessage, errorToolMessage];

    const result = getAllLastFailedActions(messages);

    // Should not include any messages since there's no AI+error pair
    expect(result).toBe("");
  });

  it("should handle a mix of error and non-error tool messages", () => {
    const aiMessage1 = new AIMessage({
      content: "First command",
      id: "ai-1",
    });

    const successToolMessage1 = new ToolMessage({
      content: "Success",
      tool_call_id: "tool-1",
      name: "shell",
      status: "success",
    });

    const aiMessage2 = new AIMessage({
      content: "Second command",
      id: "ai-2",
    });

    const errorToolMessage = new ToolMessage({
      content: "Error occurred",
      tool_call_id: "tool-2",
      name: "shell",
      status: "error",
    });

    const messages = [
      aiMessage1,
      successToolMessage1,
      aiMessage2,
      errorToolMessage,
    ];

    const result = getAllLastFailedActions(messages);

    // Should not include any messages since we encounter a success message first
    expect(result).toBe("");
  });

  it("should handle multiple tool messages after an AI message", () => {
    const aiMessage = new AIMessage({
      content: "Let me try multiple commands",
      id: "ai-1",
    });

    const errorToolMessage1 = new ToolMessage({
      content: "First command failed",
      tool_call_id: "tool-1",
      name: "shell",
      status: "error",
    });

    const errorToolMessage2 = new ToolMessage({
      content: "Second command failed",
      tool_call_id: "tool-2",
      name: "read_file",
      status: "error",
    });

    const messages = [aiMessage, errorToolMessage1, errorToolMessage2];

    const result = getAllLastFailedActions(messages);

    // Should include the AI message and the first error tool message
    expect(result).toContain("Let me try multiple commands");
    expect(result).toContain("First command failed");

    // The second error tool message should not be paired with the AI message
    // since we're looking for AI+tool pairs
    expect(result).not.toContain("Second command failed");
  });
});
