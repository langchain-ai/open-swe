import {
  BaseMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
  RemoveMessage,
} from "@langchain/core/messages";

export function removeLastTaskMessages(messages: BaseMessage[]): BaseMessage[] {
  return messages
    .filter((m) => {
      if (
        m.additional_kwargs?.summary_message ||
        (!isAIMessage(m) && !isToolMessage(m)) ||
        !m.id
      ) {
        return false;
      }
      return true;
    })
    .map((m) => new RemoveMessage({ id: m.id ?? "" }));
}

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
