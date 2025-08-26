import { AIMessage, AIMessageChunk } from "@langchain/core/messages";
import { interrupt } from "@langchain/langgraph";
import { WRITE_COMMANDS } from "./constants.js";
import { AgentStateHelpers, type CodingAgentStateType } from "./state.js";

interface StateType extends CodingAgentStateType {
  todos: any[];
  files: Record<string, string>;
  messages: any[];
}

interface ToolCall {
  name?: string;
  args?: Record<string, any>;
  [key: string]: any;
}

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
      state.approved_operations = { cached_approvals: new Set<string>() };
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

    // Update the message if any tool calls were filtered out
    if (approvedToolCalls.length !== lastMessage.tool_calls.length) {
      const originalToolCalls = lastMessage.tool_calls.filter((toolCall) =>
        approvedToolCalls.some((approved) => approved.name === toolCall.name),
      );

      const MessageClass =
        lastMessage instanceof AIMessageChunk ? AIMessageChunk : AIMessage;
      const newMessage = new MessageClass({
        content: lastMessage.content,
        tool_calls: originalToolCalls,
        additional_kwargs: lastMessage.additional_kwargs,
      });

      const newMessages = [...messages.slice(0, -1), newMessage];
      state.messages = newMessages;
    }

    return state;
  }

  return postModelHook;
}
