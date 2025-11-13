import {
  BaseMessage,
  isHumanMessage,
  HumanMessage,
} from "@langchain/core/messages";
import { getMessageContentString } from "@openswe/shared/messages";
import { extractContentWithoutDetailsFromIssueBody } from "./issue-messages.js";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { shouldCreateIssue } from "./should-create-issue.js";

export type UserRequestDetails = {
  text: string;
  featureIds: string[];
};

const sanitizeFeatureId = (candidate: string): string | undefined => {
  const trimmed = candidate
    .replace(/^[^a-z0-9]+/i, "")
    .replace(/[^a-z0-9_-]+$/i, "")
    .trim();
  if (!trimmed) {
    return undefined;
  }
  return trimmed.toLowerCase();
};

const collectFeatureIds = (rawText: string): UserRequestDetails => {
  if (!rawText) {
    return { text: "", featureIds: [] };
  }

  const featureIds = new Set<string>();
  let text = rawText;

  const parseList = (value: string): string[] =>
    value
      .split(/[\s,;]+/)
      .map(sanitizeFeatureId)
      .filter((id): id is string => Boolean(id));

  const patterns: { regex: RegExp; extract: (match: RegExpExecArray) => string[] }[] = [
    {
      regex: /\[(?:feature|features)\s*[:=]\s*([^\]]+)\]/gi,
      extract: (match) => parseList(match[1] ?? ""),
    },
    {
      regex: /<(?:feature|features)\s*[:=]\s*([^>]+)>/gi,
      extract: (match) => parseList(match[1] ?? ""),
    },
    {
      regex: /(?:^|\s)(?:#|@)?features?\s*[:=]\s*([^\n]+)/gi,
      extract: (match) => parseList((match[1] ?? "").split(/[.!?]/)[0]),
    },
    {
      regex: /(?:^|\s)(?:#|@)?features?\s*\(([^)]+)\)/gi,
      extract: (match) => parseList(match[1] ?? ""),
    },
    {
      regex: /#feature[s]?[-/]([a-z0-9_/-]+)/gi,
      extract: (match) => parseList(match[1] ?? ""),
    },
  ];

  for (const { regex, extract } of patterns) {
    text = text.replace(regex, (match, ...args) => {
      const result = extract([match, ...args] as RegExpExecArray);
      for (const featureId of result) {
        featureIds.add(featureId);
      }
      return " ";
    });
  }

  const cleanedText = text
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([,.!?])/g, "$1")
    .trim();

  return { text: cleanedText, featureIds: Array.from(featureIds) };
};

const parseRequestFromMessage = (message: HumanMessage): UserRequestDetails => {
  const parsedContent = extractContentWithoutDetailsFromIssueBody(
    getMessageContentString(message.content),
  );

  return collectFeatureIds(parsedContent);
};

const withFeatureMetadata = (
  message: HumanMessage,
  details: UserRequestDetails,
): HumanMessage =>
  new HumanMessage({
    ...message,
    content: details.text,
    additional_kwargs: {
      ...message.additional_kwargs,
      ...(details.featureIds.length > 0 ? { featureIds: details.featureIds } : {}),
    },
  });

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

  const details = parseRequestFromMessage(initialMessage);

  return options?.returnFullMessage
    ? withFeatureMetadata(initialMessage, details)
    : details.text;
}

export function getInitialUserRequestDetails(
  messages: BaseMessage[],
): UserRequestDetails {
  const initialMessage = messages.findLast(
    (m) => isHumanMessage(m) && m.additional_kwargs?.isOriginalIssue,
  );

  if (!initialMessage) {
    return { text: "", featureIds: [] };
  }

  return parseRequestFromMessage(initialMessage);
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

  if (
    options?.config &&
    (isLocalMode(options.config) || !shouldCreateIssue(options.config))
  ) {
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

  const details = parseRequestFromMessage(recentUserMessage);

  return options?.returnFullMessage
    ? withFeatureMetadata(recentUserMessage, details)
    : details.text;
}

export function getRecentUserRequestDetails(
  messages: BaseMessage[],
  options?: { config?: GraphConfig },
): UserRequestDetails {
  const recentMessage = (() => {
    if (
      options?.config &&
      (isLocalMode(options.config) || !shouldCreateIssue(options.config))
    ) {
      return messages.findLast(isHumanMessage);
    }

    return messages.findLast(
      (m) => isHumanMessage(m) && m.additional_kwargs?.isFollowup,
    );
  })();

  if (!recentMessage) {
    return { text: "", featureIds: [] };
  }

  return parseRequestFromMessage(recentMessage);
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
  const { text: initialRequest } = getInitialUserRequestDetails(messages);
  const userRequest = initialRequest || noRequestMessage;
  const { text: followupRequest } = getRecentUserRequestDetails(messages);

  if (followupRequest) {
    return followupRequestPrompt
      .replace("{USER_REQUEST}", userRequest)
      .replace("{USER_FOLLOWUP_REQUEST}", followupRequest);
  }

  return singleRequestPrompt.replace("{USER_REQUEST}", userRequest);
}
