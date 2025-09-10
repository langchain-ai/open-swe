import { MessageContent } from "@langchain/core/messages";
import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "Messages");

export function getMessageContentString(content: MessageContent): string {
  try {
    if (typeof content === "string") return content;

    return content
      .filter((c): c is { type: "text"; text: string } => c.type === "text")
      .map((c) => c.text)
      .join(" ");
  } catch (error) {
    logger.error("Failed to get message content string", error);
    return "";
  }
}
