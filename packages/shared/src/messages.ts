import { MessageContent } from "@langchain/core/messages";

export function getMessageContentString(content: MessageContent): string {
  try {
    if (typeof content === "string") return content;

    return content
      .filter((c): c is { type: "text"; text: string } => c.type === "text")
      .map((c) => c.text)
      .join(" ");
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error("Failed to get message content string", error);
    return "";
  }
}
