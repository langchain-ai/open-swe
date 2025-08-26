import { AIMessage, ToolMessage, HumanMessage } from "@langchain/core/messages";
import { describe, expect, test } from "@jest/globals";
import {
  calculateErrorRate,
  groupToolMessagesByAIMessage,
  shouldDiagnoseError,
} from "../utils/tool-message-error.js";

// Helper function to create a tool message with the specified parameters
function createToolMessage(
  tool_call_id: string,
  name: string,
  status: "success" | "error",
  is_diagnosis: boolean = false,
): ToolMessage {
  const message = new ToolMessage({
    tool_call_id,
    content: `Result of ${name}`,
    name,
    status,
    ...(is_diagnosis ? { additional_kwargs: { is_diagnosis: true } } : {}),
  });

  return message;
}

describe("Error diagnosis logic", () => {
  describe("groupToolMessagesByAIMessage", () => {
    test("should group tool messages by their parent AI message", () => {
      const messages = [
        new HumanMessage({ content: "Human response" }),
        new AIMessage({ content: "AI message 1" }),
        createToolMessage("1", "tool1", "success"),
        createToolMessage("2", "tool2", "error"),
        new HumanMessage({ content: "Human response" }),
        new AIMessage({ content: "AI message 2" }),
        createToolMessage("3", "tool3", "success"),
        createToolMessage("4", "tool4", "success"),
        createToolMessage("5", "tool5", "error"),
      ];

      const groups = groupToolMessagesByAIMessage(messages);

      expect(groups.length).toBe(2);
      expect(groups[0].length).toBe(2); // First group has 2 tool messages
      expect(groups[1].length).toBe(3); // Second group has 3 tool messages
    });

    test("should filter out diagnostic tool messages", () => {
      const messages = [
        new HumanMessage({ content: "Human response" }),
        new AIMessage({ content: "AI message" }),
        createToolMessage("1", "tool1", "success"),
        createToolMessage("2", "tool2", "error", true),
        createToolMessage("3", "tool3", "error"),
      ];

      const groups = groupToolMessagesByAIMessage(messages);

      expect(groups.length).toBe(1);
      expect(groups[0].length).toBe(2); // Only non-diagnostic tools
      expect(groups[0][0].tool_call_id).toBe("1");
      expect(groups[0][1].tool_call_id).toBe("3");
    });
  });

  describe("calculateErrorRate", () => {
    test("should return 0 for empty group", () => {
      expect(calculateErrorRate([])).toBe(0);
    });

    test("should calculate correct error rate", () => {
      const group = [
        createToolMessage("1", "tool1", "success"),
        createToolMessage("2", "tool2", "error"),
        createToolMessage("3", "tool3", "error"),
        createToolMessage("4", "tool4", "success"),
      ];

      expect(calculateErrorRate(group)).toBe(0.5); // 2 errors out of 4 = 50%
    });

    test("should return 1 for all errors", () => {
      const group = [
        createToolMessage("1", "tool1", "error"),
        createToolMessage("2", "tool2", "error"),
      ];

      expect(calculateErrorRate(group)).toBe(1); // 100% errors
    });
  });

  describe("shouldDiagnoseError", () => {
    test("should return false if less than 3 groups", () => {
      const messages = [
        new HumanMessage({ content: "Human response" }),
        new AIMessage({ content: "AI message 1" }),
        createToolMessage("1", "tool1", "error"),
        createToolMessage("2", "tool2", "error"),
        new AIMessage({ content: "AI message 2" }), // AI message 2
        createToolMessage("3", "tool3", "error"),
        createToolMessage("4", "tool4", "error"),
      ];

      expect(shouldDiagnoseError(messages)).toBe(false);
    });

    test("should return true if last three groups all have >= 75% error rate", () => {
      const messages = [
        new HumanMessage({ content: "Human response" }),
        new AIMessage({ content: "AI message 1" }), // AI message 1 (not part of last 3)
        createToolMessage("1", "tool1", "success"),
        createToolMessage("2", "tool2", "success"),

        new AIMessage({ content: "AI message 2" }), // AI message 2 (part of last 3)
        createToolMessage("3", "tool3", "error"),
        createToolMessage("4", "tool4", "error"),
        createToolMessage("5", "tool5", "error"),
        createToolMessage("6", "tool6", "success"), // 75% error rate

        new AIMessage({ content: "AI message 3" }), // AI message 3 (part of last 3)
        createToolMessage("7", "tool7", "error"),
        createToolMessage("8", "tool8", "error"),
        createToolMessage("9", "tool9", "error"), // 100% error rate

        new AIMessage({ content: "AI message 4" }), // AI message 4 (part of last 3)
        createToolMessage("10", "tool10", "error"),
        createToolMessage("11", "tool11", "error"),
        createToolMessage("12", "tool12", "success"),
        createToolMessage("13", "tool13", "error"), // 75% error rate
      ];

      expect(shouldDiagnoseError(messages)).toBe(true);
    });

    test("should return false if any of the last three groups has < 75% error rate", () => {
      const messages = [
        new AIMessage({ content: "AI message 1" }), // AI message 1
        createToolMessage("1", "tool1", "error"),
        createToolMessage("2", "tool2", "error"),

        new AIMessage({ content: "AI message 2" }), // AI message 2
        createToolMessage("3", "tool3", "error"),
        createToolMessage("4", "tool4", "error"),
        createToolMessage("5", "tool5", "error"),

        new AIMessage({ content: "AI message 3" }), // AI message 3
        createToolMessage("6", "tool6", "success"),
        createToolMessage("7", "tool7", "success"),
        createToolMessage("8", "tool8", "error"), // 33% error rate (below threshold)

        new AIMessage({ content: "AI message 4" }), // AI message 4
        createToolMessage("9", "tool9", "error"),
        createToolMessage("10", "tool10", "error"),
      ];

      expect(shouldDiagnoseError(messages)).toBe(false);
    });

    test("should ignore diagnostic tool messages when calculating error rates", () => {
      const messages = [
        new AIMessage({ content: "AI message 0" }), // AI message 0 (NOT part of last 3)
        createToolMessage("0", "tool0", "error"),
        createToolMessage("1", "diagnose_error", "success", true), // Old diagnostic (ignored and outside last 3)

        new AIMessage({ content: "AI message 1" }), // AI message 1 (part of last 3)
        createToolMessage("2", "tool1", "error"),
        createToolMessage("3", "tool2", "error"),

        new AIMessage({ content: "AI message 2" }), // AI message 2 (part of last 3)
        createToolMessage("4", "tool3", "error"),
        createToolMessage("5", "tool4", "error"),

        new AIMessage({ content: "AI message 3" }), // AI message 3 (part of last 3)
        createToolMessage("6", "tool5", "error"),
        createToolMessage("7", "tool6", "error"),
      ];

      expect(shouldDiagnoseError(messages)).toBe(true); // All 3 groups have 100% error rate, no recent diagnosis
    });

    test("should return false if there was a diagnosis tool call in the last 3 groups", () => {
      const messages = [
        new AIMessage({ content: "AI message 1" }), // AI message 1 (part of last 3)
        createToolMessage("1", "tool1", "error"),
        createToolMessage("2", "tool2", "error"),
        createToolMessage("3", "tool3", "error"),
        createToolMessage("4", "tool4", "error"), // 100% error rate

        new AIMessage({ content: "AI message 2" }), // AI message 2 (part of last 3)
        createToolMessage("5", "tool5", "error"),
        createToolMessage("6", "tool6", "error"),
        createToolMessage("7", "tool7", "error"),
        createToolMessage("8", "diagnose_error", "success", true), // Diagnosis tool call

        new AIMessage({ content: "AI message 3" }), // AI message 3 (part of last 3)
        createToolMessage("9", "tool9", "error"),
        createToolMessage("10", "tool10", "error"),
        createToolMessage("11", "tool11", "error"), // 100% error rate
      ];

      expect(shouldDiagnoseError(messages)).toBe(false); // Should not diagnose due to recent diagnosis
    });

    test("should return true if diagnosis tool call was more than 3 groups ago", () => {
      const messages = [
        new AIMessage({ content: "AI message 1" }), // AI message 1 (NOT part of last 3)
        createToolMessage("1", "tool1", "error"),
        createToolMessage("2", "diagnose_error", "success", true), // Diagnosis tool call (old)

        new AIMessage({ content: "AI message 2" }), // AI message 2 (part of last 3)
        createToolMessage("3", "tool3", "error"),
        createToolMessage("4", "tool4", "error"),
        createToolMessage("5", "tool5", "error"),
        createToolMessage("6", "tool6", "success"), // 75% error rate

        new AIMessage({ content: "AI message 3" }), // AI message 3 (part of last 3)
        createToolMessage("7", "tool7", "error"),
        createToolMessage("8", "tool8", "error"),
        createToolMessage("9", "tool9", "error"), // 100% error rate

        new AIMessage({ content: "AI message 4" }), // AI message 4 (part of last 3)
        createToolMessage("10", "tool10", "error"),
        createToolMessage("11", "tool11", "error"),
        createToolMessage("12", "tool12", "success"),
        createToolMessage("13", "tool13", "error"), // 75% error rate
      ];

      expect(shouldDiagnoseError(messages)).toBe(true); // Should diagnose since old diagnosis is outside last 3 groups
    });

    test("should return false if diagnosis tool call is in the most recent group", () => {
      const messages = [
        new AIMessage({ content: "AI message 1" }), // AI message 1 (part of last 3)
        createToolMessage("1", "tool1", "error"),
        createToolMessage("2", "tool2", "error"),
        createToolMessage("3", "tool3", "error"), // 100% error rate

        new AIMessage({ content: "AI message 2" }), // AI message 2 (part of last 3)
        createToolMessage("4", "tool4", "error"),
        createToolMessage("5", "tool5", "error"),
        createToolMessage("6", "tool6", "error"), // 100% error rate

        new AIMessage({ content: "AI message 3" }), // AI message 3 (part of last 3)
        createToolMessage("7", "tool7", "error"),
        createToolMessage("8", "tool8", "error"),
        createToolMessage("9", "tool9", "error"),
        createToolMessage("10", "diagnose_error", "success", true), // Recent diagnosis
      ];

      expect(shouldDiagnoseError(messages)).toBe(false); // Should not diagnose due to recent diagnosis
    });
  });
});
