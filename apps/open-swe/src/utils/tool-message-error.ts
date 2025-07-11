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
 * Whether or not to route to the diagnose error step. This is true if:
 * - the last three tool call groups all have >= 75% error rates
 *
 * TBD: Should this be checking that each of the last 3 have >= 75% error rates,
 * or >= 75% error rate of all tool messages from the last 3 groups?
 *
 * @param messages All messages to analyze
 */
export function shouldDiagnoseError(messages: Array<any>) {
  // Group tool messages by their parent AI message
  const toolGroups = groupToolMessagesByAIMessage(messages);

  // If we don't have at least 3 groups, we can't make a determination
  if (toolGroups.length < 3) return false;

  // Get the last three groups
  const lastThreeGroups = toolGroups.slice(-3);

  // Check if all of the last three groups have an error rate >= 75%
  const ERROR_THRESHOLD = 0.75; // 75%
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
