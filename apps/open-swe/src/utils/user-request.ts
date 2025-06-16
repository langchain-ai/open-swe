import {
  BaseMessage,
  isHumanMessage,
  HumanMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";

export function getUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: never | false },
): string;
export function getUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: true },
): HumanMessage;
export function getUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: boolean },
): string | HumanMessage {
  const recentUserMessage = messages.findLast(
    (m) => isHumanMessage(m) && m.additional_kwargs?.isOriginalIssue,
  );
  if (!recentUserMessage) {
    return "";
  }
  return options?.returnFullMessage
    ? recentUserMessage
    : getMessageContentString(recentUserMessage.content);
}
