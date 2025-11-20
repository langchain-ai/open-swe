import {
  AIMessage,
  BaseMessage,
  HumanMessage,
  isAIMessage,
  isHumanMessage,
  isSystemMessage,
  isToolMessage,
  SystemMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { ToolCall } from "@langchain/core/messages/tool";
import { getMessageContentString } from "@openswe/shared/messages";

export function getToolCallsString(toolCalls: ToolCall[] | undefined): string {
  if (!toolCalls?.length) return "";
  return toolCalls.map((c) => JSON.stringify(c, null, 2)).join("\n");
}

export function getAIMessageString(message: AIMessage): string {
  const content = getMessageContentString(message.content);
  const toolCalls = getToolCallsString(message.tool_calls);
  return `<assistant message-id=${message.id ?? "No ID"}>\nContent: ${content}\nTool calls: ${toolCalls}\n</assistant>`;
}

export function getHumanMessageString(message: HumanMessage): string {
  const content = getMessageContentString(message.content);
  return `<human message-id=${message.id ?? "No ID"}>\nContent: ${content}\n</human>`;
}

export function getToolMessageString(message: ToolMessage): string {
  const content = getMessageContentString(message.content);
  const toolCallId = message.tool_call_id;
  const toolCallName = message.name;
  const toolStatus = message.status || "success";

  return `<tool message-id=${message.id ?? "No ID"} status="${toolStatus}">\nTool Call ID: ${toolCallId}\nTool Call Name: ${toolCallName}\nContent: ${content}\n</tool>`;
}

export function getSystemMessageString(message: SystemMessage): string {
  const content = getMessageContentString(message.content);
  return `<system message-id=${message.id ?? "No ID"}>\nContent: ${content}\n</system>`;
}

export function getUnknownMessageString(message: BaseMessage): string {
  return `<unknown message-id=${message.id ?? "No ID"}>\n${JSON.stringify(message, null, 2)}\n</unknown>`;
}

export function getMessageString(message: BaseMessage): string {
  if (isAIMessage(message)) {
    return getAIMessageString(message);
  } else if (isHumanMessage(message)) {
    return getHumanMessageString(message);
  } else if (isToolMessage(message)) {
    return getToolMessageString(message);
  } else if (isSystemMessage(message)) {
    return getSystemMessageString(message);
  }

  return getUnknownMessageString(message);
}

export function filterMessagesWithoutContent(
  messages: BaseMessage[],
  filterHidden = true,
): BaseMessage[] {
  return messages.filter((m) => {
    if (filterHidden && m.additional_kwargs?.hidden) {
      return false;
    }
    const messageContentStr = getMessageContentString(m.content);
    if (!isAIMessage(m)) {
      if (isToolMessage(m)) {
        return !!messageContentStr || !!m.tool_call_id;
      }
      return !!messageContentStr;
    }
    const toolCallsCount = m.tool_calls?.length || 0;
    return !!messageContentStr || toolCallsCount > 0;
  });
}

export function sanitizeMessagesWithToolCallResponses(
  messages: BaseMessage[],
): BaseMessage[] {
  const toolCallsWithResponses = new Set<string>();
  const toolCallsSeen = new Set<string>();

  messages.forEach((message) => {
    if (isAIMessage(message) && message.tool_calls?.length) {
      message.tool_calls
        .filter((toolCall) => !!toolCall.id)
        .forEach((toolCall) => toolCallsSeen.add(toolCall.id as string));
    }

    if (isToolMessage(message) && message.tool_call_id) {
      if (toolCallsSeen.has(message.tool_call_id)) {
        toolCallsWithResponses.add(message.tool_call_id);
      }
    }
  });

  return messages
    .map((message) => {
      if (isAIMessage(message) && message.tool_calls?.length) {
        const filteredToolCalls = message.tool_calls.filter((toolCall) =>
          toolCall.id ? toolCallsWithResponses.has(toolCall.id) : false,
        );
        const hasContent = !!getMessageContentString(message.content);

        if (!hasContent && filteredToolCalls.length === 0) {
          return null;
        }

        if (filteredToolCalls.length === message.tool_calls.length) {
          return message;
        }

        return new AIMessage({
          content: message.content,
          tool_calls: filteredToolCalls,
          additional_kwargs: message.additional_kwargs,
          response_metadata: message.response_metadata,
          name: message.name,
          id: message.id,
        });
      }

      if (
        isToolMessage(message) &&
        message.tool_call_id &&
        !toolCallsWithResponses.has(message.tool_call_id)
      ) {
        return null;
      }

      return message;
    })
    .filter((message): message is BaseMessage => message !== null);
}
