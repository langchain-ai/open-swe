import { MessageContent } from "@langchain/core/messages";

export function getMessageContentString(content: MessageContent): string {
  if (typeof content === "string") return content;

  return content
    .filter((c): c is { type: "text"; text: string } => c.type === "text")
    .map((c) => c.text)
    .join(" ");
}
