import { AIMessage, AIMessageChunk } from "@langchain/core/messages";
import { interrupt } from "@langchain/langgraph";
import { WRITE_COMMANDS } from "./constants.js";
import { AgentStateHelpers, type CodingAgentStateType } from "./state.js";
import { ToolCall } from "@langchain/core/messages/tool";
import { ApprovedOperations } from "./types.js";

type StateType = CodingAgentStateType;


export function createAgentPostModelHook() {
  /**
   * Post model hook that checks for write tool calls and uses caching to avoid
   * redundant approval prompts for the same command/directory combinations.
   */
  async function postModelHook(state: StateType): Promise<StateType> {
    // Get the last message from the state
    const messages = state.messages || [];
    if (messages.length === 0) {
      return state;
    }

    const lastMessage = messages[messages.length - 1];

    if (
      !(
        lastMessage instanceof AIMessage ||
        lastMessage instanceof AIMessageChunk
      ) ||
      !lastMessage.tool_calls
    ) {
      return state;
    }

    if (!state.approved_operations) {
      const approved_operations: ApprovedOperations = {
        cached_approvals: new Set<string>(),
      };
      state.approved_operations = approved_operations;
    }

    const approvedToolCalls: ToolCall[] = [];

    for (const toolCall of lastMessage.tool_calls) {
      const toolName = toolCall.name || "";
      const toolArgs = toolCall.args || {};

      // Skip tool calls without a name
      if (!toolCall.name) {
        continue;
      }

      if (WRITE_COMMANDS.has(toolName)) {
        // Check if this command/directory combination has been approved before
        if (AgentStateHelpers.isOperationApproved(state, toolName, toolArgs)) {
          approvedToolCalls.push(toolCall);
        } else {
          const approvalKey = AgentStateHelpers.getApprovalKey(
            toolName,
            toolArgs,
          );

          const isApproved = interrupt({
            command: toolName,
            args: toolArgs,
            approval_key: approvalKey,
          });

          if (isApproved) {
            AgentStateHelpers.addApprovedOperation(state, toolName, toolArgs);
            approvedToolCalls.push(toolCall);
          } else {
            continue;
          }
        }
      } else {
        approvedToolCalls.push(toolCall);
      }
    }

    // Return the updated message if any tool calls were filtered out
    if (approvedToolCalls.length !== lastMessage.tool_calls.length) {
      const originalToolCalls = lastMessage.tool_calls.filter((toolCall) =>
        approvedToolCalls.some((approved) => approved.name === toolCall.name),
      );

      const newMessage = new AIMessage({
        ...lastMessage,
        tool_calls: originalToolCalls,
      });

      // Update the messages in the state
      const newMessages = [...messages.slice(0, -1), newMessage];
      state.messages = newMessages;
    }

    return state;
  }

  return postModelHook;
}
