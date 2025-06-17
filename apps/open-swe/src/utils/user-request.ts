import {
  BaseMessage,
  isHumanMessage,
  HumanMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";

// TODO: Might want a better way of doing this.
// maybe add a new kwarg `isRequest` and have this return the last human message with that field?
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
