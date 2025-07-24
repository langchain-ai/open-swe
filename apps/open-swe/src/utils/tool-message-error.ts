import {
  BaseMessage,
  isAIMessage,
  isToolMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { getMessageString } from "./message/content.js";

/**
 * Group tool messages by their parent AI message
 * @param messages Array of messages to process
 * @returns Array of tool message groups, where each group contains tool messages tied to the same AI message
 */
export function groupToolMessagesByAIMessage(
  messages: Array<any>,
): ToolMessage[][] {
  const groups: ToolMessage[][] = [];
  let currentGroup: ToolMessage[] = [];
  let processingToolsForAI = false;

  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];

    if (isAIMessage(message)) {
      // If we were already processing tools for a previous AI message, save that group
      if (currentGroup.length > 0) {
        groups.push([...currentGroup]);
        currentGroup = [];
      }
      processingToolsForAI = true;
    } else if (
      isToolMessage(message) &&
      processingToolsForAI &&
      !message.additional_kwargs?.is_diagnosis
    ) {
      currentGroup.push(message);
    } else if (!isToolMessage(message) && processingToolsForAI) {
      // We've encountered a non-tool message after an AI message, end the current group
      if (currentGroup.length > 0) {
        groups.push([...currentGroup]);
        currentGroup = [];
      }
      processingToolsForAI = false;
    }
  }

  // Add the last group if it exists
  if (currentGroup.length > 0) {
    groups.push(currentGroup);
  }

  return groups;
}

/**
 * Calculate the error rate for a group of tool messages
 * @param group Array of tool messages
 * @returns Error rate as a number between 0 and 1
 */
export function calculateErrorRate(group: ToolMessage[]): number {
  if (group.length === 0) return 0;
  const errorCount = group.filter((m) => m.status === "error").length;
  return errorCount / group.length;
}

/**
 * Check if there was a diagnosis tool call within the last N tool message groups
 * @param messages Array of messages to check
 * @param groupCount Number of recent groups to check
 * @returns True if a diagnosis tool call was found in the recent groups
 */
function hasRecentDiagnosisToolCall(
  messages: Array<BaseMessage>,
  groupCount: number,
): boolean {
  const allGroups: ToolMessage[][] = [];
  let currentGroup: ToolMessage[] = [];
  let processingToolsForAI = false;

  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];

    if (isAIMessage(message)) {
      if (currentGroup.length > 0) {
        allGroups.push([...currentGroup]);
        currentGroup = [];
      }
      processingToolsForAI = true;
    } else if (isToolMessage(message) && processingToolsForAI) {
      currentGroup.push(message);
    } else if (!isToolMessage(message) && processingToolsForAI) {
      if (currentGroup.length > 0) {
        allGroups.push([...currentGroup]);
        currentGroup = [];
      }
      processingToolsForAI = false;
    }
  }

  if (currentGroup.length > 0) {
    allGroups.push(currentGroup);
  }

  const recentGroups = allGroups.slice(-groupCount);
  return recentGroups.some((group) =>
    group.some((message) => message.additional_kwargs?.is_diagnosis),
  );
}

/**
 * Whether or not to route to the diagnose error step. This is true if:
 * - the last three tool call groups all have >= 75% error rates
 * - there hasn't been a diagnose error tool call within the last three message groups
 *
 * @param messages All messages to analyze
 */
export function shouldDiagnoseError(messages: Array<BaseMessage>) {
  const toolGroups = groupToolMessagesByAIMessage(messages);

  if (toolGroups.length < 3) return false;

  const lastThreeGroups = toolGroups.slice(-3);

  const hasRecentDiagnosis = hasRecentDiagnosisToolCall(messages, 3);
  if (hasRecentDiagnosis) return false;

  const ERROR_THRESHOLD = 0.75;
  return lastThreeGroups.every(
    (group) => calculateErrorRate(group) >= ERROR_THRESHOLD,
  );
}

export const getAllLastFailedActions = (messages: BaseMessage[]): string => {
  const result: string[] = [];
  let i = 0;

  // Find pairs of AI messages followed by error tool messages
  while (i < messages.length - 1) {
    const currentMessage = messages[i];
    const nextMessage = messages[i + 1];

    if (
      isAIMessage(currentMessage) &&
      isToolMessage(nextMessage) &&
      nextMessage?.status === "error"
    ) {
      // Add the AI message and its corresponding error tool message
      result.push(getMessageString(currentMessage));
      result.push(getMessageString(nextMessage));
      i += 2; // Move to the next potential pair
    } else if (
      isToolMessage(currentMessage) &&
      currentMessage?.status !== "error"
    ) {
      // Stop when we encounter a non-error tool message
      break;
    } else {
      // Move to the next message if current one doesn't match our pattern
      i++;
    }
  }

  return result.join("\n");
};
