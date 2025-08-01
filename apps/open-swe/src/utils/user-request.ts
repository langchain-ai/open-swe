import {
  BaseMessage,
  isHumanMessage,
  HumanMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@open-swe/shared/messages";
import { extractContentWithoutDetailsFromIssueBody } from "./github/issue-messages.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { GraphConfig } from "@open-swe/shared/open-swe/types";

// TODO: Might want a better way of doing this.
// maybe add a new kwarg `isRequest` and have this return the last human message with that field?
export function getInitialUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: never | false },
): string;
export function getInitialUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: true },
): HumanMessage;
export function getInitialUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: boolean },
): string | HumanMessage {
  const initialMessage = messages.findLast(
    (m) => isHumanMessage(m) && m.additional_kwargs?.isOriginalIssue,
  );

  if (!initialMessage) {
    return "";
  }

  const parsedContent = extractContentWithoutDetailsFromIssueBody(
    getMessageContentString(initialMessage.content),
  );
  return options?.returnFullMessage
    ? new HumanMessage({
        ...initialMessage,
        content: parsedContent,
      })
    : parsedContent;
}

export function getRecentUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: never | false; config?: GraphConfig },
): string;
export function getRecentUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: true; config?: GraphConfig },
): HumanMessage;
export function getRecentUserRequest(
  messages: BaseMessage[],
  options?: { returnFullMessage?: boolean; config?: GraphConfig },
): string | HumanMessage {
  let recentUserMessage: HumanMessage | undefined;

  if (options?.config && isLocalMode(options.config)) {
    // In local mode, get the last human message regardless of flags
    recentUserMessage = messages.findLast(isHumanMessage);
  } else {
    // In normal mode, look for messages with isFollowup flag
    recentUserMessage = messages.findLast(
      (m) => isHumanMessage(m) && m.additional_kwargs?.isFollowup,
    );
  }

  if (!recentUserMessage) {
    return "";
  }

  const parsedContent = extractContentWithoutDetailsFromIssueBody(
    getMessageContentString(recentUserMessage.content),
  );
  return options?.returnFullMessage
    ? new HumanMessage({
        ...recentUserMessage,
        content: parsedContent,
      })
    : parsedContent;
}

const DEFAULT_SINGLE_USER_REQUEST_PROMPT = `Here is the user's request:
{USER_REQUEST}`;

const DEFAULT_USER_SENDING_FOLLOWUP_PROMPT = `Here is the user's initial request:
{USER_REQUEST}

And here is the user's followup request you're now processing:
{USER_FOLLOWUP_REQUEST}`;

export function formatUserRequestPrompt(
  messages: BaseMessage[],
  singleRequestPrompt: string = DEFAULT_SINGLE_USER_REQUEST_PROMPT,
  followupRequestPrompt: string = DEFAULT_USER_SENDING_FOLLOWUP_PROMPT,
): string {
  const noRequestMessage = "No user request provided.";
  const userRequest = getInitialUserRequest(messages) || noRequestMessage;
  const userFollowupRequest = getRecentUserRequest(messages);

  if (userFollowupRequest) {
    return followupRequestPrompt
      .replace("{USER_REQUEST}", userRequest)
      .replace("{USER_FOLLOWUP_REQUEST}", userFollowupRequest);
  }

  return singleRequestPrompt.replace("{USER_REQUEST}", userRequest);
}
