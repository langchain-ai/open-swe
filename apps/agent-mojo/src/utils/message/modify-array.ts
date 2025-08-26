import { BaseMessage, isHumanMessage } from "@langchain/core/messages";

export function removeFirstHumanMessage(
  messages: BaseMessage[],
): BaseMessage[] {
  let humanMsgFound = false;
  return messages.filter((m) => {
    if (isHumanMessage(m) && !humanMsgFound) {
      humanMsgFound = true;
      return false;
    }

    return true;
  });
}

export function removeLastHumanMessage(messages: BaseMessage[]): BaseMessage[] {
  const lastHumanMessage = messages.findLast(isHumanMessage);
  if (!lastHumanMessage) {
    return messages;
  }
  return messages.filter((m) => m.id !== lastHumanMessage.id);
}
