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
import { getMessageContentString } from "@open-swe/shared/messages";

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
      return !!messageContentStr;
    }
    const toolCallsCount = m.tool_calls?.length || 0;
    return !!messageContentStr || toolCallsCount > 0;
  });
}
