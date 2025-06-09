import { isAIMessage } from "@langchain/core/messages";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { PlannerGraphState } from "../subgraphs/planner/types.js";

const logger = createLogger(LogLevel.INFO, "GetGenUIMessageId");

export function getGenUIMessageIdAndContent<
  StateType extends GraphState | PlannerGraphState,
>(
  state: StateType,
): {
  id: string;
  aiMessageId: string;
  content: string;
} {
  const messages =
    "plannerMessages" in state ? state.plannerMessages : state.messages;
  const lastMessage = messages.findLast(isAIMessage);
  const genUiId = lastMessage?.additional_kwargs?.gen_ui_id;
  if (!genUiId || typeof genUiId !== "string") {
    const errorMessage = "Failed to get gen_ui_id from last message";
    logger.error(errorMessage, {
      lastMessage,
    });
    throw new Error(errorMessage);
  }

  if (!lastMessage.id) {
    const errorMessage = "Failed to get ID from last message";
    logger.error(errorMessage, {
      lastMessage,
    });
    throw new Error(errorMessage);
  }

  return {
    id: genUiId,
    aiMessageId: lastMessage.id,
    content: getMessageContentString(lastMessage.content),
  };
}
