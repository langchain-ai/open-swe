import { BaseMessage } from "@langchain/core/messages";

export function filterHiddenMessages(messages: BaseMessage[]): BaseMessage[] {
  return messages.filter((message) => !message.additional_kwargs?.hidden);
}
