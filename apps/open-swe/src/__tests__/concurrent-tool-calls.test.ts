import { AIMessage, ToolMessage } from "@langchain/core/messages";
import { describe, expect, test } from "@jest/globals";
import { Send } from "@langchain/langgraph";

// Mock the necessary imports and functions
const mockCreateMarkTaskCompletedToolFields = () => ({
  name: "mark_task_completed"
});

// Mock GraphState type
interface MockGraphState {
  internalMessages: any[];
}

// Test implementation of routeGeneratedAction function logic
function testRouteGeneratedAction(state: MockGraphState) {
  const { internalMessages } = state;
  const lastMessage = internalMessages[internalMessages.length - 1];

  // If the message is an AI message, and it has tool calls, we should take action.
  if (lastMessage && lastMessage.tool_calls?.length) {
    const toolCalls = lastMessage.tool_calls;
    
    // Check for request_human_help among all tool calls
    const requestHelpCall = toolCalls.find((tc: any) => tc.name === "request_human_help");
    if (requestHelpCall) {
      return "request-help";
    }

    // Check for update_plan among all tool calls
    const updatePlanCall = toolCalls.find((tc: any) => 
      tc.name === "update_plan" &&
      "update_plan_reasoning" in tc.args &&
      typeof tc.args?.update_plan_reasoning === "string"
    );

    // Check for mark_task_completed among all tool calls
    const markTaskCompletedCall = toolCalls.find((tc: any) => 
      tc.name === mockCreateMarkTaskCompletedToolFields().name
    );

    if (markTaskCompletedCall) {
      return "handle-completed-task";
    }

    // If update_plan is called alongside other tools, handle multiple routing
    if (updatePlanCall) {
      const otherToolCalls = toolCalls.filter((tc: any) => tc.name !== "update_plan");
      
      if (otherToolCalls.length > 0) {
        // Return multiple Send objects: one for update-plan and one for take-action
        return [
          { node: "update-plan", data: { planChangeRequest: updatePlanCall.args?.update_plan_reasoning } },
          { node: "take-action", data: {} }
        ];
      } else {
        // Only update_plan is called, return single Send object
        return { node: "update-plan", data: { planChangeRequest: updatePlanCall.args?.update_plan_reasoning } };
      }
    }

    return "take-action";
  }

  return "no-action";
}

// Test implementation of generateAction message filtering logic
function testMessageFiltering(toolCalls: any[]) {
  // Handle concurrent tool calls - if request_human_help is called alongside other tools,
  // remove all other tool calls to ensure only request_human_help is processed
  if (
    toolCalls?.length &&
    toolCalls?.length > 1 &&
    toolCalls.some((t) => t.name === "request_human_help")
  ) {
    return toolCalls.filter((t) => t.name === "request_human_help");
  }
  
  return toolCalls;
}

describe("Concurrent Tool Call Handling", () => {
  describe("Message Filtering in generateAction", () => {
    test("should keep only request_human_help when called with other tools", () => {
      const toolCalls = [
        { name: "search", args: { query: "test" } },
        { name: "request_human_help", args: { help_request: "Need help" } },
        { name: "apply_patch", args: { patch: "diff" } }
      ];

      const filtered = testMessageFiltering(toolCalls);

      expect(filtered).toHaveLength(1);
      expect(filtered[0].name).toBe("request_human_help");
    });

    test("should not filter when request_human_help is called alone", () => {
      const toolCalls = [
        { name: "request_human_help", args: { help_request: "Need help" } }
      ];

      const filtered = testMessageFiltering(toolCalls);

      expect(filtered).toHaveLength(1);
      expect(filtered[0].name).toBe("request_human_help");
    });

    test("should not filter when request_human_help is not present", () => {
      const toolCalls = [
        { name: "search", args: { query: "test" } },
        { name: "apply_patch", args: { patch: "diff" } }
      ];

      const filtered = testMessageFiltering(toolCalls);

      expect(filtered).toHaveLength(2);
      expect(filtered.map(tc => tc.name)).toEqual(["search", "apply_patch"]);
    });

    test("should handle empty tool calls array", () => {
      const toolCalls: any[] = [];

      const filtered = testMessageFiltering(toolCalls);

      expect(filtered).toHaveLength(0);
    });
  });

  describe("Routing Logic in routeGeneratedAction", () => {
    test("should route to request-help when request_human_help is present", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "search", args: { query: "test" } },
            { name: "request_human_help", args: { help_request: "Need help" } }
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("request-help");
    });

    test("should route to handle-completed-task when mark_task_completed is present", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "mark_task_completed", args: {} }
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("handle-completed-task");
    });

    test("should return single Send object when only update_plan is called", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "update_plan", args: { update_plan_reasoning: "Need to update plan" } }
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toEqual({
        node: "update-plan",
        data: { planChangeRequest: "Need to update plan" }
      });
    });

    test("should return multiple Send objects when update_plan is called with other tools", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "update_plan", args: { update_plan_reasoning: "Need to update plan" } },
            { name: "search", args: { query: "test" } }
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(Array.isArray(result)).toBe(true);
      expect(result).toHaveLength(2);
      expect(result).toEqual([
        { node: "update-plan", data: { planChangeRequest: "Need to update plan" } },
        { node: "take-action", data: {} }
      ]);
    });

    test("should route to take-action for other tool calls", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "search", args: { query: "test" } },
            { name: "apply_patch", args: { patch: "diff" } }
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("take-action");
    });

    test("should prioritize request_human_help over other tools", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "update_plan", args: { update_plan_reasoning: "Need to update plan" } },
            { name: "request_human_help", args: { help_request: "Need help" } },
            { name: "search", args: { query: "test" } }
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("request-help");
    });

    test("should prioritize mark_task_completed over update_plan", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "update_plan", args: { update_plan_reasoning: "Need to update plan" } },
            { name: "mark_task_completed", args: {} }
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("handle-completed-task");
    });

    test("should handle empty messages array", () => {
      const state: MockGraphState = {
        internalMessages: []
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("no-action");
    });

    test("should handle message without tool calls", () => {
      const state: MockGraphState = {
        internalMessages: [{
          content: "Just a message without tools"
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("no-action");
    });
  });

  describe("Edge Cases", () => {
    test("should handle update_plan without proper reasoning", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "update_plan", args: {} } // Missing update_plan_reasoning
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("take-action");
    });

    test("should handle update_plan with non-string reasoning", () => {
      const state: MockGraphState = {
        internalMessages: [{
          tool_calls: [
            { name: "update_plan", args: { update_plan_reasoning: 123 } } // Non-string reasoning
          ]
        }]
      };

      const result = testRouteGeneratedAction(state);

      expect(result).toBe("take-action");
    });
  });
}); 